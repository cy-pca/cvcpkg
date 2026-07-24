# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""OIDC identity for human users (roadmap Phase 13).

cvcpkg delegates **human** authentication to an external OIDC provider rather
than building account management, password handling, and permission UX from
scratch.  HMAC API tokens remain the mechanism for **machines** (CI, builders,
scripted publishes) — the right tool for each audience.

Flow: standard **authorization code** flow for a confidential client.

    /admin/oidc/login  -> redirect to the IdP (state + nonce + PKCE S256)
    /admin/oidc/callback -> verify state, exchange code at the token endpoint
                            (direct, TLS, client_secret), read claims from the
                            userinfo endpoint, map them to a cvcpkg role, and
                            mint the normal signed admin session cookie.

**On id_token signature verification:** the tokens are fetched by *direct
server-to-server TLS* communication with the token endpoint, so per OIDC Core
§3.1.3.7 the TLS server validation stands in for checking the token signature.
Claims are then read from the userinfo endpoint over TLS with the access
token.  This keeps cvcpkg free of a JWT/JWKS dependency; if a deployment needs
local id_token signature validation, that is a hardening follow-up.

Config (env):

    CVCPKG_OIDC_ISSUER          e.g. https://accounts.example.com
    CVCPKG_OIDC_CLIENT_ID
    CVCPKG_OIDC_CLIENT_SECRET
    CVCPKG_OIDC_REDIRECT_URL    e.g. https://cvcpkg.org/admin/oidc/callback
    CVCPKG_OIDC_SCOPES          default "openid email profile"
    CVCPKG_OIDC_GROUPS_CLAIM    claim holding the user's groups (default "groups")
    CVCPKG_OIDC_ADMIN_GROUPS    comma-separated groups granted the admin role
    CVCPKG_OIDC_PUBLISHER_GROUPS  comma-separated groups granted publisher
    CVCPKG_OIDC_ADMIN_EMAILS    comma-separated emails granted admin (fallback
                                for IdPs that do not emit groups)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field

_TXN_COOKIE = "cvcpkg_oidc_txn"
_TXN_TTL_SECONDS = 600  # 10 minutes to complete a login


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ── Config ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class OidcConfig:
    """OIDC provider + claim-mapping configuration."""

    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_url: str = ""
    scopes: str = "openid email profile"
    groups_claim: str = "groups"
    admin_groups: frozenset[str] = field(default_factory=frozenset)
    publisher_groups: frozenset[str] = field(default_factory=frozenset)
    admin_emails: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls) -> OidcConfig:
        return cls(
            issuer=os.environ.get("CVCPKG_OIDC_ISSUER", "").strip().rstrip("/"),
            client_id=os.environ.get("CVCPKG_OIDC_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("CVCPKG_OIDC_CLIENT_SECRET", "").strip(),
            redirect_url=os.environ.get("CVCPKG_OIDC_REDIRECT_URL", "").strip(),
            scopes=os.environ.get("CVCPKG_OIDC_SCOPES", "openid email profile").strip(),
            groups_claim=os.environ.get("CVCPKG_OIDC_GROUPS_CLAIM", "groups").strip(),
            admin_groups=_csv_set(os.environ.get("CVCPKG_OIDC_ADMIN_GROUPS", "")),
            publisher_groups=_csv_set(os.environ.get("CVCPKG_OIDC_PUBLISHER_GROUPS", "")),
            admin_emails=_csv_set(os.environ.get("CVCPKG_OIDC_ADMIN_EMAILS", "")),
        )

    def is_enabled(self) -> bool:
        """OIDC login is offered only when the provider is fully configured."""
        return bool(self.issuer and self.client_id and self.client_secret and self.redirect_url)

    @property
    def discovery_url(self) -> str:
        return f"{self.issuer}/.well-known/openid-configuration"


# ── Claim -> role mapping (pure) ────────────────────────────────


def map_claims_to_role(claims: dict, cfg: OidcConfig) -> str | None:
    """Map IdP claims onto a cvcpkg role name, or None if unauthorized.

    Precedence: admin groups -> admin emails -> publisher groups -> None.
    Returning None means the user authenticated with the IdP but has no
    cvcpkg entitlement — they are refused rather than silently downgraded.
    """
    raw_groups = claims.get(cfg.groups_claim) or []
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    groups = {str(g) for g in raw_groups}

    email = str(claims.get("email") or "").lower()

    if cfg.admin_groups and (groups & cfg.admin_groups):
        return "admin"
    if cfg.admin_emails and email and email in {e.lower() for e in cfg.admin_emails}:
        return "admin"
    if cfg.publisher_groups and (groups & cfg.publisher_groups):
        return "publisher"
    return None


def claims_subject(claims: dict) -> str:
    """A stable, human-readable identity for audit records."""
    return str(claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "")


# ── PKCE + signed login transaction (pure) ──────────────────────


def new_pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def sign_txn(key: bytes, payload: dict, *, now: float | None = None) -> str:
    """Serialise + HMAC-sign a short-lived login transaction.

    Carries the PKCE verifier, the state nonce, and the OIDC nonce across the
    redirect in an HttpOnly cookie — the verifier must never travel via the
    ``state`` query parameter, which is visible to the IdP and the browser's
    address bar.
    """
    body = dict(payload)
    body["exp"] = int((now if now is not None else time.time()) + _TXN_TTL_SECONDS)
    raw = _b64url(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_txn(key: bytes, value: str, *, now: float | None = None) -> dict | None:
    """Verify + decode a login transaction cookie, or None if invalid/expired."""
    try:
        raw, sig = value.split(".", 1)
    except (ValueError, AttributeError):
        return None
    want = hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        body = json.loads(base64.urlsafe_b64decode(raw + pad).decode())
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    if (now if now is not None else time.time()) >= float(body.get("exp", 0)):
        return None
    return body


def build_authorize_url(
    authorization_endpoint: str, cfg: OidcConfig, *, state: str, nonce: str, challenge: str
) -> str:
    """Build the IdP authorization redirect (code flow + PKCE S256)."""
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_url,
        "scope": cfg.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


# ── IdP calls (network) ─────────────────────────────────────────


async def discover(cfg: OidcConfig, *, timeout: float = 15.0) -> dict:
    """Fetch the provider's OpenID discovery document."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(cfg.discovery_url)
        resp.raise_for_status()
        return resp.json()


async def exchange_code(
    token_endpoint: str, cfg: OidcConfig, *, code: str, verifier: str, timeout: float = 15.0
) -> dict:
    """Exchange an authorization code for tokens (direct, TLS, confidential)."""
    import httpx

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_url,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(token_endpoint, data=data, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(
    userinfo_endpoint: str, access_token: str, *, timeout: float = 15.0
) -> dict:
    """Read the authenticated user's claims from the userinfo endpoint."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        return resp.json()
