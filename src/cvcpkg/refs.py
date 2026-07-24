# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Dependency reference parsing for federated cross-server dependencies.

A dependency may be qualified by an organization and/or a federated **registry
host**.  The host is a *logical* name (``edge-b.lab``), never a full URL: the
local registries config (:func:`cvcpkg.config.registry_for`) maps it to a base
URL + token and *is* the trust allowlist, so a recipe can never steer the
resolver at an arbitrary host.

Accepted string forms (plus the structured ``{name, version, org, server}`` dict)::

    zlib                          public, current server
    zlib@1.2                      + version constraint
    shell/iqi-core                org 'shell', current server
    shell/iqi-core@^1             + version
    cvc://edge-b.lab/iqi-core     public on registry host 'edge-b.lab'
    cvc://edge-b.lab/shell/iqi    org 'shell' on 'edge-b.lab'
    cvc://edge-b.lab:8420/shell/x host may carry a port
"""

from __future__ import annotations

from dataclasses import dataclass

_SCHEMES = ("cvc://", "https://", "http://")


def _norm_host(h: str) -> str:
    """Reduce a server value to a bare ``host[:port]`` (strip scheme/trailing /)."""
    h = (h or "").strip()
    for scheme in _SCHEMES:
        if h.startswith(scheme):
            h = h[len(scheme) :]
            break
    return h.split("/", 1)[0].rstrip("/")


def _split_version(s: str) -> tuple[str, str]:
    # The version constraint is whatever follows the last '@'.  Package and org
    # slugs never contain '@', so rpartition is unambiguous.
    if "@" in s:
        head, _, ver = s.rpartition("@")
        return head, ver
    return s, ""


@dataclass(frozen=True)
class DepRef:
    """A parsed dependency reference."""

    name: str
    version: str = ""
    org: str = ""
    server: str = ""  # bare host[:port]; "" = current server

    @property
    def qualified_name(self) -> str:
        """``org/name`` when org-scoped, else ``name``."""
        return f"{self.org}/{self.name}" if self.org else self.name

    def to_uri(self) -> str:
        """Canonical ``cvc://`` string (server-qualified) or a bare/org name."""
        base = self.qualified_name
        if self.version:
            base = f"{base}@{self.version}"
        return f"cvc://{self.server}/{base}" if self.server else base


def parse_dep_ref(ref: object) -> DepRef:
    """Parse a dependency reference (str, dict, or DepRef) into a DepRef."""
    if isinstance(ref, DepRef):
        return ref
    if isinstance(ref, dict):
        name = str(ref.get("name", "") or "").strip()
        if not name:
            raise ValueError(f"dependency dict requires a 'name': {ref!r}")
        return DepRef(
            name=name,
            version=str(ref.get("version", "") or ""),
            org=str(ref.get("org", "") or ""),
            server=_norm_host(str(ref.get("server", "") or "")),
        )
    if not isinstance(ref, str):
        raise ValueError(f"invalid dependency reference: {ref!r}")

    s = ref.strip()
    if not s:
        raise ValueError("empty dependency reference")

    server = ""
    if s.startswith("cvc://"):
        rest = s[len("cvc://") :]
        host, _, path = rest.partition("/")
        server = _norm_host(host)
        if not server:
            raise ValueError(f"cvc:// reference missing host: {ref!r}")
        if not path:
            raise ValueError(f"cvc:// reference missing package: {ref!r}")
        s = path

    head, version = _split_version(s)
    parts = [p for p in head.split("/")]
    if len(parts) == 1:
        org, name = "", parts[0]
    elif len(parts) == 2:
        org, name = parts[0], parts[1]
    else:
        raise ValueError(f"too many '/' segments in dependency reference: {ref!r}")

    if not name:
        raise ValueError(f"dependency reference missing package name: {ref!r}")
    if org and "." in org:
        # A dotted first segment is almost certainly a host typed without the
        # cvc:// scheme — reject it rather than silently treat it as an org.
        raise ValueError(
            f"dependency org '{org}' looks like a host; use cvc://{org}/... for "
            f"a federated registry reference: {ref!r}"
        )
    return DepRef(name=name, version=version, org=org, server=server)
