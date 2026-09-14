# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Client-side storage for ``cvcpkg login`` sessions — one per server host.

A session obtained by ``cvcpkg login`` (an opaque ``cvcses_`` access token plus a
rotating ``cvcref_`` refresh token) is kept here, keyed by host, deliberately the
same shape as ``registries.yaml`` because ``config.authorize_request`` already
decides what to send by a host lookup.

Runtime dependencies are stdlib + PyYAML only (the client path must load on
Haiku, OpenBSD and the single-binary build); the refresh call uses
``urllib.request``, never httpx.

Precedence for what token reaches the server (highest first):
    --token flag  >  CVCPKG_TOKEN env  >  this file (for the request's host).
So a CI runner exporting ``CVCPKG_TOKEN`` never consults this file and behaves
exactly as before.
"""

from __future__ import annotations

import datetime
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from cvcpkg.envfile import warn_if_world_readable

_VERSION = 1
# Refresh a little BEFORE the access token actually expires, so a request that
# starts just under the wire does not race the clock and 401.
_REFRESH_SKEW_SECONDS = 60


@dataclass
class Credential:
    """One host's stored session."""

    access: str = ""
    refresh: str = ""
    expires_at: str = ""  # ISO-8601; when the access token expires
    refresh_expires_at: str = ""
    max_lifetime_at: str = ""
    principal: str = ""
    role: str = ""
    issuer: str = ""
    subject: str = ""
    device: str = ""
    session_id: int | None = None
    server_url: str = ""

    @classmethod
    def from_token_response(cls, data: dict, *, server_url: str) -> Credential:
        """Build a Credential from a ``/v1/auth/token`` (or device) response."""
        now = datetime.datetime.now(datetime.timezone.utc)

        def _iso_in(seconds) -> str:
            try:
                return (now + datetime.timedelta(seconds=int(seconds))).isoformat()
            except (TypeError, ValueError):
                return ""

        return cls(
            access=str(data.get("access_token", "")),
            refresh=str(data.get("refresh_token", "")),
            expires_at=_iso_in(data.get("expires_in", 0)),
            refresh_expires_at=_iso_in(data.get("refresh_expires_in", 0)),
            max_lifetime_at=str(data.get("max_lifetime_at", "")),
            principal=str(data.get("principal", "")),
            role=str(data.get("role", "")),
            issuer=str(data.get("issuer", "")),
            subject=str(data.get("subject", "")),
            device=str(data.get("device", "")),
            session_id=data.get("session_id"),
            server_url=server_url,
        )


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _path() -> Path:
    override = os.environ.get("CVCPKG_CREDENTIALS_FILE")
    if override:
        return Path(override)
    from cvcpkg.config import _default_config_dir

    return _default_config_dir() / "credentials.yaml"


def load() -> dict[str, Credential]:
    path = _path()
    if not path.is_file():
        return {}
    warn_if_world_readable(path)
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    hosts = data.get("hosts") if isinstance(data, dict) else None
    out: dict[str, Credential] = {}
    if isinstance(hosts, dict):
        for host, raw in hosts.items():
            if isinstance(raw, dict):
                fields = {k: raw.get(k) for k in Credential().__dict__ if k in raw}
                out[str(host)] = Credential(**fields)
    return out


def _write(creds: dict[str, Credential]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"version": _VERSION, "hosts": {h: asdict(c) for h, c in creds.items()}},
        default_flow_style=False,
        sort_keys=True,
    )
    # 0600, atomic: write a private temp file then rename over the target.
    tmp = path.with_name(path.name + ".tmp")
    if os.name == "nt":
        tmp.write_text(body)
    else:
        fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, body.encode())
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def get(host: str) -> Credential | None:
    return load().get(host.lower())


def save(host: str, cred: Credential) -> None:
    creds = load()
    creds[host.lower()] = cred
    _write(creds)


def remove(host: str) -> bool:
    creds = load()
    if creds.pop(host.lower(), None) is None:
        return False
    _write(creds)
    return True


def _is_expired(iso: str, *, skew: int = 0) -> bool:
    if not iso:
        return True
    try:
        exp = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) >= (exp - datetime.timedelta(seconds=skew))


def _post_form(url: str, form: dict[str, str], *, timeout: float = 30.0) -> dict | None:
    from urllib.parse import urlencode

    body = urlencode(form).encode()
    req = urllib.request.Request(  # noqa: S310 - our own server, scheme fixed by caller
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None


def refresh(host: str, cred: Credential) -> Credential | None:
    """Exchange the stored refresh token for a fresh pair; persist and return it.

    Returns None if the refresh is unusable (expired/reused/revoked); the caller
    should then prompt for a fresh ``cvcpkg login``.  Persisting the rotated
    refresh is mandatory — dropping the successor locks the user out.
    """
    server = cred.server_url or f"https://{host}"
    if not cred.refresh or _is_expired(cred.refresh_expires_at):
        return None
    data = _post_form(
        f"{server.rstrip('/')}/v1/auth/token",
        {"grant_type": "refresh_token", "refresh_token": cred.refresh, "client_id": "cvcpkg-cli"},
    )
    if not data or not data.get("access_token"):
        return None
    fresh = Credential.from_token_response(data, server_url=server)
    save(host, fresh)
    return fresh


def token_for(host: str) -> str:
    """Return a usable access token for *host*, refreshing if it has expired.

    Best-effort: on a refresh failure the stale access token is returned and the
    server's 401 surfaces normally, rather than crashing an unrelated command.
    """
    cred = get(host)
    if cred is None or not cred.access:
        return ""
    if _is_expired(cred.expires_at, skew=_REFRESH_SKEW_SECONDS):
        refreshed = refresh(host, cred)
        if refreshed is not None:
            return refreshed.access
    return cred.access


def logout_local(host: str) -> bool:
    """Remove the stored credential for *host* (local half of logout)."""
    return remove(host)
