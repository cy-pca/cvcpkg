# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""``cvcpkg login`` client flows — stdlib only (no httpx, no third-party deps).

Two grants, matching the server broker:

* **loopback** (desktop) — bind an ephemeral ``127.0.0.1`` port, open the browser
  to ``/v1/auth/authorize``, and receive the authorization code on that port,
  then exchange it with PKCE.  The raw ``cvcses_`` never appears in any URL.
* **pairing** (headless / SSH) — start a device pairing, print an 8-char code for
  the human to approve in any browser, and poll until a session is collected.

Everything here is ``http.server`` + ``webbrowser`` + ``urllib.request`` +
``secrets``/``hashlib``/``base64``/``json`` — all present on Windows, macOS,
Linux, OpenBSD and Haiku, and what keeps the single-binary client small.
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import secrets
import socket
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from cvcpkg.credentials import Credential

CLIENT_ID = "cvcpkg-cli"


class LoginError(Exception):
    """A login attempt failed for a reason worth showing the user."""


def _client_version() -> str:
    try:
        from cvcpkg import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def _default_device_label() -> str:
    try:
        return socket.gethostname()[:128] or "cvcpkg-cli"
    except Exception:
        return "cvcpkg-cli"


def _platform_tag() -> str:
    return f"{sys.platform}-{platform.machine()}"[:64]


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


def _post(
    url: str, *, json_body: dict | None = None, form: dict | None = None, timeout: float = 30.0
):
    """POST and return ``(status, parsed_json_or_None)``; never raises on HTTP error."""
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers = {"Content-Type": "application/json"}
    else:
        data = urlencode(form or {}).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - our server
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, _read_json(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, _read_json(exc)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LoginError(f"could not reach {urlsplit(url).netloc}: {exc}") from exc


def _read_json(resp) -> dict | None:
    try:
        return json.loads(resp.read().decode())
    except (ValueError, OSError):
        return None


# ── Loopback (desktop) ──────────────────────────────────────────


class _CallbackHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/callback":
            self.send_error(404)
            return
        q = parse_qs(parts.query)
        type(self).captured = {
            "code": (q.get("code") or [""])[0],
            "state": (q.get("state") or [""])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><meta charset=utf-8><title>cvcpkg</title>"
            b"<body style='font-family:sans-serif;background:#111;color:#eee;"
            b"text-align:center;padding:4rem'>"
            b"<h2>You're signed in to cvcpkg.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
        )

    def log_message(self, *a):  # keep the terminal clean
        pass


def loopback_login(
    server_url: str,
    *,
    role: str = "",
    device: str = "",
    port: int = 0,
    open_browser: bool = True,
    timeout: float = 300.0,
    printer=print,
) -> Credential:
    """Run the loopback authorization-code grant and return the Credential."""
    import webbrowser

    server_url = server_url.rstrip("/")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)

    _CallbackHandler.captured = {}
    try:
        httpd = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    except OSError as exc:
        raise LoginError(f"could not bind a loopback port: {exc}") from exc
    bound_port = httpd.server_address[1]
    redirect_uri = f"http://127.0.0.1:{bound_port}/callback"

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "device": device or _default_device_label(),
    }
    if role:
        params["role"] = role
    authorize_url = f"{server_url}/v1/auth/authorize?{urlencode(params)}"

    printer(f"Opening your browser to sign in to {urlsplit(server_url).netloc} …")
    printer(f"  If it does not open, visit:\n    {authorize_url}\n")
    opened = webbrowser.open(authorize_url) if open_browser else False
    if not opened and open_browser:
        printer("  (could not launch a browser automatically — open the URL above)")

    httpd.timeout = timeout
    deadline = time.time() + timeout
    try:
        while not _CallbackHandler.captured and time.time() < deadline:
            httpd.handle_request()
    finally:
        httpd.server_close()

    captured = _CallbackHandler.captured
    if not captured or not captured.get("code"):
        raise LoginError("timed out waiting for the browser sign-in")
    if not secrets.compare_digest(captured.get("state", ""), state):
        raise LoginError("state mismatch on the loopback callback — login refused")

    status, data = _post(
        f"{server_url}/v1/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": captured["code"],
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
        },
    )
    if status != 200 or not data or not data.get("access_token"):
        raise LoginError(
            f"token exchange failed ({status}): {(data or {}).get('error', 'unknown')}"
        )
    return Credential.from_token_response(data, server_url=server_url)


# ── Device pairing (headless) ───────────────────────────────────


def pairing_login(
    server_url: str,
    *,
    role: str = "",
    device: str = "",
    timeout: float = 600.0,
    printer=print,
    sleep=time.sleep,
) -> Credential:
    """Run the device-pairing grant: print a code, poll until approved."""
    server_url = server_url.rstrip("/")
    verifier = secrets.token_urlsafe(32)
    verifier_hash = hashlib.sha256(verifier.encode()).hexdigest()

    status, data = _post(
        f"{server_url}/v1/auth/device",
        json_body={
            "client_id": CLIENT_ID,
            "device_label": device or _default_device_label(),
            "platform": _platform_tag(),
            "client_version": _client_version(),
            "verifier_hash": verifier_hash,
            "requested_role": role,
        },
    )
    if status != 200 or not data or not data.get("pairing_id"):
        raise LoginError(f"could not start pairing ({status})")
    pairing_id = data["pairing_id"]
    user_code = data.get("user_code", "")
    uri = data.get("verification_uri", "/link")
    uri_complete = data.get("verification_uri_complete", uri)
    interval = max(int(data.get("interval", 5) or 5), 1)

    printer(f"\nPairing this machine with {urlsplit(server_url).netloc}\n")
    printer(f"  1. On any device, open:  {uri}")
    printer(f"  2. Enter this code:      {user_code}\n")
    printer(f"  (or open directly: {uri_complete})\n")
    printer("Waiting for approval… (Ctrl-C to cancel)")

    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            sleep(interval)
            st, body = _post(
                f"{server_url}/v1/auth/device/token",
                json_body={"pairing_id": pairing_id, "verifier": verifier},
            )
            if st == 200 and body and body.get("access_token"):
                return Credential.from_token_response(body, server_url=server_url)
            err = (body or {}).get("error", "")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = max(
                    int((body or {}).get("interval", interval * 2) or interval * 2), interval
                )
                continue
            if err in ("access_denied", "expired_token"):
                raise LoginError(f"pairing {err.replace('_', ' ')}")
            # Any other shape: keep waiting until the deadline.
        raise LoginError("timed out waiting for approval")
    except KeyboardInterrupt:
        _post(
            f"{server_url}/v1/auth/device/cancel",
            json_body={"pairing_id": pairing_id, "verifier": verifier},
        )
        raise LoginError("cancelled") from None


def can_open_browser() -> bool:
    """Whether a loopback+browser login is plausibly usable on this host."""
    import os

    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
