# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Browser sessions for principals.

The cookie is a **reference, not a bearer**.  It carries a signed payload, but
every request re-reads the ``sessions`` row it names and re-checks the
principal behind it.  That is what makes signing out mean something: a signed
stateless cookie cannot be invalidated, so "log out" against one is a
suggestion that the client discard a credential which still works.  The
existing admin cookie has exactly that shape, which is why this is a new
mechanism rather than an extension of it.

The payload is still signed and still carries the role, so the common path
costs one indexed primary-key lookup rather than a re-derivation of identity.

Key separation: these are signed with ``derive_key(hmac_key, "user-session")``,
not the raw token-hashing key.  A forgery primitive found in one signing
context must not carry into another.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json

from sqlalchemy import select, update

from cvcpkg.server.auth import derive_key
from cvcpkg.server.db import PrincipalRow, SessionRow, atomic_session, get_session

COOKIE_NAME = "cvcpkg_session"
_VERSION = "v1"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_cookie(key: bytes, payload: dict) -> str:
    """Serialise + sign a session cookie value."""
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(derive_key(key, "user-session"), body.encode(), hashlib.sha256).hexdigest()
    return f"{_VERSION}.{body}.{sig}"


def decode_cookie(key: bytes, value: str) -> dict | None:
    """Verify + decode a session cookie value, or None.

    Signature first, then expiry.  This does not consult the database — the
    caller does that, and must, because everything revocable lives there.
    """
    try:
        version, body, sig = value.split(".", 2)
    except (ValueError, AttributeError):
        return None
    if version != _VERSION:
        return None
    want = hmac.new(derive_key(key, "user-session"), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return None
    try:
        payload = json.loads(_unb64url(body).decode())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        return None
    if datetime.datetime.now(datetime.timezone.utc).timestamp() >= exp:
        return None
    return payload


class DbSessionStore:
    """Sessions, keyed to principals."""

    def __init__(self, hmac_key: bytes) -> None:
        self._key = hmac_key

    async def mint(
        self,
        principal: PrincipalRow,
        role: str,
        *,
        ttl_seconds: int,
        device_label: str = "",
        ip: str = "",
    ) -> str:
        """Create a session row and return the cookie value for it."""
        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = now + datetime.timedelta(seconds=ttl_seconds)
        async with atomic_session() as session:
            row = SessionRow(
                principal_id=principal.id,
                role=role,
                device_label=device_label[:128],
                ip_at_issue=ip[:64],
                expires_at=expires_at,
            )
            session.add(row)
            await session.flush()
            sid = row.id
            name = principal.name
            issuer = principal.issuer
            subject = principal.subject
        return encode_cookie(
            self._key,
            {
                "sid": sid,
                "pid": principal.id,
                "name": name,
                "role": role,
                "iss": issuer,
                "sub": subject,
                "iat": int(now.timestamp()),
                "exp": int(expires_at.timestamp()),
            },
        )

    async def resolve(self, cookie_value: str) -> tuple[SessionRow, PrincipalRow] | None:
        """Verify a cookie and load the live session + principal behind it.

        Returns None when the signature fails, the payload has expired, the
        session row is missing/revoked/expired, or the principal is disabled.
        Disabling a principal therefore ends every one of its sessions on the
        next request, with no sweep required.
        """
        payload = decode_cookie(self._key, cookie_value or "")
        if payload is None:
            return None
        sid = payload.get("sid")
        if not isinstance(sid, int):
            return None

        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (
                (await session.execute(select(SessionRow).where(SessionRow.id == sid)))
                .scalars()
                .first()
            )
            if row is None or row.revoked:
                return None
            expires_at = row.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
            if expires_at is not None and expires_at < now:
                return None
            principal = (
                (
                    await session.execute(
                        select(PrincipalRow).where(PrincipalRow.id == row.principal_id)
                    )
                )
                .scalars()
                .first()
            )
            if principal is None or principal.disabled:
                return None
            return row, principal

    async def revoke(self, session_id: int, *, principal_id: int | None = None) -> bool:
        """Revoke one session, optionally requiring it to belong to *principal_id*."""
        async with atomic_session() as session:
            stmt = update(SessionRow).where(
                SessionRow.id == session_id, SessionRow.revoked == False  # noqa: E712
            )
            if principal_id is not None:
                stmt = stmt.where(SessionRow.principal_id == principal_id)
            result = await session.execute(stmt.values(revoked=True))
            return result.rowcount > 0

    async def revoke_all_for_principal(
        self, principal_id: int, *, except_session_id: int | None = None
    ) -> int:
        async with atomic_session() as session:
            stmt = update(SessionRow).where(
                SessionRow.principal_id == principal_id,
                SessionRow.revoked == False,  # noqa: E712
            )
            if except_session_id is not None:
                stmt = stmt.where(SessionRow.id != except_session_id)
            result = await session.execute(stmt.values(revoked=True))
            return int(result.rowcount or 0)

    async def list_for_principal(self, principal_id: int) -> list[SessionRow]:
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(SessionRow)
                        .where(
                            SessionRow.principal_id == principal_id,
                            SessionRow.revoked == False,  # noqa: E712
                            SessionRow.expires_at > now,
                        )
                        .order_by(SessionRow.issued_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def count_active_for_principal(self, principal_id: int) -> int:
        return len(await self.list_for_principal(principal_id))
