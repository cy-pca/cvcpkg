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
import os
from typing import NamedTuple

from sqlalchemy import and_, delete, or_, select, update

from cvcpkg.server.auth import derive_key
from cvcpkg.server.db import PrincipalRow, SessionRow, atomic_session, get_session

COOKIE_NAME = "cvcpkg_session"
_VERSION = "v1"

# CLI credentials are opaque bearers (not signed cookies): the raw value is
# never decodable and only its hash is stored, so a stolen row cannot be turned
# back into a usable secret.
ACCESS_PREFIX = "cvcses_"
REFRESH_PREFIX = "cvcref_"


class MintedSession(NamedTuple):
    """The result of minting/rotating a CLI session — enough for a token response."""

    access: str
    refresh: str
    session_id: int
    expires_at: datetime.datetime
    refresh_expires_at: datetime.datetime
    max_lifetime_at: datetime.datetime
    principal_name: str
    role: str


def _cli_max_role_ceiling():
    """The CVCPKG_CLI_MAX_ROLE ceiling as a TokenRole (default admin)."""
    from cvcpkg.server.models import TokenRole

    val = (os.environ.get("CVCPKG_CLI_MAX_ROLE", "admin") or "admin").strip()
    try:
        return TokenRole(val)
    except ValueError:
        return TokenRole.admin


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

    # ── CLI opaque-bearer sessions (cvcses_ / cvcref_) ──────────────

    def _hash_bearer(self, raw: str) -> str:
        return hmac.new(derive_key(self._key, "session"), raw.encode(), hashlib.sha256).hexdigest()

    async def mint_cli(
        self,
        principal: PrincipalRow,
        role: str,
        *,
        ttl_seconds: int,
        refresh_ttl_seconds: int,
        max_lifetime_seconds: int,
        device_label: str = "",
        client_id: str = "",
        ip: str = "",
        family: str | None = None,
        max_lifetime_at: datetime.datetime | None = None,
    ) -> MintedSession:
        """Create a CLI session row and return its secrets + metadata.

        ``family``/``max_lifetime_at`` are threaded through by ``rotate`` so a
        refreshed successor keeps the original hard-reauth horizon and family.
        """
        import secrets as _secrets

        now = datetime.datetime.now(datetime.timezone.utc)
        access = ACCESS_PREFIX + _secrets.token_urlsafe(32)
        refresh = REFRESH_PREFIX + _secrets.token_urlsafe(32)
        fam = family or _secrets.token_urlsafe(16)
        horizon = max_lifetime_at or (now + datetime.timedelta(seconds=max_lifetime_seconds))
        # Neither the access nor the refresh may outlive the hard horizon.
        access_exp = min(now + datetime.timedelta(seconds=ttl_seconds), horizon)
        refresh_exp = min(now + datetime.timedelta(seconds=refresh_ttl_seconds), horizon)
        async with atomic_session() as session:
            row = SessionRow(
                principal_id=principal.id,
                role=role,
                device_label=device_label[:128],
                client_id=client_id[:64],
                ip_at_issue=ip[:64],
                expires_at=access_exp,
                token_hash=self._hash_bearer(access),
                refresh_hash=self._hash_bearer(refresh),
                refresh_family=fam,
                refresh_used=False,
                refresh_expires_at=refresh_exp,
                max_lifetime_at=horizon,
            )
            session.add(row)
            await session.flush()
            sid = row.id
        return MintedSession(
            access=access,
            refresh=refresh,
            session_id=sid,
            expires_at=access_exp,
            refresh_expires_at=refresh_exp,
            max_lifetime_at=horizon,
            principal_name=principal.name,
            role=role,
        )

    async def verify_bearer(self, raw: str):
        """Verify a ``cvcses_`` access token → a TokenRecord, or None.

        The returned record's ``.name`` is the *principal* name, so every
        downstream authorization predicate treats it exactly like a token named
        after the person.  Role is clamped to the principal's current
        entitlement, and a disabled principal is refused — instantly, on every
        request, with no sweep.
        """
        from cvcpkg.server.models import TokenRecord, TokenRole
        from cvcpkg.server.principals import min_role

        if not raw or not raw.startswith(ACCESS_PREFIX):
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        token_hash = self._hash_bearer(raw)
        async with get_session() as session:
            row = (
                (
                    await session.execute(
                        select(SessionRow).where(SessionRow.token_hash == token_hash)
                    )
                )
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
            # Clamp to BOTH the principal's current entitlement AND the CLI
            # ceiling, so lowering CVCPKG_CLI_MAX_ROLE bounds live sessions on
            # their next request, not just new grants.
            eff_role = min_role(
                min_role(TokenRole(row.role), TokenRole(principal.last_role)),
                _cli_max_role_ceiling(),
            )
            return TokenRecord(
                name=principal.name,
                role=eff_role,
                token_hash="",
                email=principal.email,
                expires_at=expires_at,
                credential_kind="session",
                credential_name=principal.name,
                principal_id=principal.id,
                session_id=row.id,
            )

    async def rotate(
        self,
        refresh_raw: str,
        *,
        ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> MintedSession | None:
        """Exchange a refresh token for a fresh ``(access, refresh)`` pair.

        Rotation is single-use with reuse detection: burning the presented
        refresh is a conditional UPDATE; if it does not win (already used, or
        revoked), the *entire family* is revoked and None is returned, because a
        replayed refresh is the signature of a stolen credential.  The successor
        inherits the family and the original hard-reauth horizon.
        """
        if not refresh_raw or not refresh_raw.startswith(REFRESH_PREFIX):
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        refresh_hash = self._hash_bearer(refresh_raw)
        async with atomic_session() as session:
            row = (
                (
                    await session.execute(
                        select(SessionRow).where(SessionRow.refresh_hash == refresh_hash)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            fam = row.refresh_family
            # Reuse detection: a burned or revoked refresh means the family is
            # compromised — revoke all of it and refuse.
            if row.refresh_used or row.revoked:
                if fam:
                    await session.execute(
                        update(SessionRow)
                        .where(SessionRow.refresh_family == fam)
                        .values(revoked=True)
                    )
                return None
            horizon = row.max_lifetime_at
            if horizon is not None and horizon.tzinfo is None:
                horizon = horizon.replace(tzinfo=datetime.timezone.utc)
            refresh_exp = row.refresh_expires_at
            if refresh_exp is not None and refresh_exp.tzinfo is None:
                refresh_exp = refresh_exp.replace(tzinfo=datetime.timezone.utc)
            if refresh_exp is not None and refresh_exp < now:
                return None
            if horizon is not None and horizon <= now:
                return None
            # Atomic single-use burn.
            burned = await session.execute(
                update(SessionRow)
                .where(SessionRow.id == row.id, SessionRow.refresh_used == False)  # noqa: E712
                .values(refresh_used=True)
            )
            if burned.rowcount != 1:
                if fam:
                    await session.execute(
                        update(SessionRow)
                        .where(SessionRow.refresh_family == fam)
                        .values(revoked=True)
                    )
                return None
            principal_id = row.principal_id
            role = row.role
            device_label = row.device_label
            client_id = row.client_id
            ip = row.ip_at_issue
            principal = (
                (await session.execute(select(PrincipalRow).where(PrincipalRow.id == principal_id)))
                .scalars()
                .first()
            )
            if principal is None or principal.disabled:
                return None
        # Successor keeps the family and the original horizon.
        return await self.mint_cli(
            principal,
            role,
            ttl_seconds=ttl_seconds,
            refresh_ttl_seconds=refresh_ttl_seconds,
            max_lifetime_seconds=0,
            device_label=device_label,
            client_id=client_id,
            ip=ip,
            family=fam,
            max_lifetime_at=horizon,
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

    async def expire_stale(self) -> int:
        """Delete dead session rows so the table does not grow without bound.

        A row is dead when it is revoked, past its hard reauth horizon, or its
        access token has expired AND it has no still-valid refresh (so a live
        CLI session mid-refresh-window is preserved; a browser cookie session,
        which has no refresh, is removed once its access expires).
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        async with atomic_session() as session:
            result = await session.execute(
                delete(SessionRow).where(
                    or_(
                        SessionRow.revoked == True,  # noqa: E712
                        and_(
                            SessionRow.max_lifetime_at.is_not(None),
                            SessionRow.max_lifetime_at < now,
                        ),
                        and_(
                            SessionRow.expires_at < now,
                            or_(
                                SessionRow.refresh_expires_at.is_(None),
                                SessionRow.refresh_expires_at < now,
                            ),
                        ),
                    )
                )
            )
            return int(result.rowcount or 0)
