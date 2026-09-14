# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The CLI-login broker: cvcpkg.org is the authorization server.

``cvcpkg login`` obtains an opaque ``cvcses_`` session (never a raw API token)
by one of two grants, both branches of this one module and both resolving to a
single principal identity:

* **loopback** (desktop default) — the CLI opens the browser to
  ``/v1/auth/authorize``, the human signs in through the identity provider, and
  a short single-use code is redirected to ``http://127.0.0.1:<port>/callback``
  and exchanged at ``/v1/auth/token`` with PKCE.
* **pairing** (headless default) — see ``pairing.py``.

Pure helpers (loopback policy, PKCE, code hashing, the client registry) live at
the top so they unit-test without a database; ``DbCliAuthStore`` holds the
short-lived rows and their atomic single-use burns.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from cvcpkg.server.auth import derive_key
from cvcpkg.server.db import (
    CliAuthCodeRow,
    CliLoginTxnRow,
    CliPairingRow,
    CodeAttemptRow,
    atomic_session,
    get_session,
)

# The only clients that may drive these grants.  A built-in constant, never a
# database table and never a wildcard: a public CLI client with a loopback
# redirect is the whole surface, so there is nothing to register or spoof.
CLI_CLIENTS: dict[str, dict] = {
    "cvcpkg-cli": {"public": True, "redirect": "loopback"},
}

# A pairing/collection is offered for this long before it expires.
DEFAULT_PAIRING_TTL_SECONDS = 600
# An authorization code (loopback leg) is redeemable for this long.
AUTH_CODE_TTL_SECONDS = 60
# A parked login transaction (authorize -> IdP -> resume) lives this long.
LOGIN_TXN_TTL_SECONDS = 600
# Brute-force lockout on user-code submission.
MAX_CODE_FAILURES = 10
CODE_ATTEMPT_WINDOW_SECONDS = 600


# ── Pure policy ─────────────────────────────────────────────────


def loopback_redirect_ok(uri: str) -> bool:
    """RFC 8252 §7.3/§8.3: only an http loopback-IP ``/callback`` on a high port.

    ``localhost`` is refused deliberately — it is DNS-resolvable and therefore
    steerable; a literal loopback IP is not.  We can apply our own port-agnostic
    matcher because the identity provider never sees this URI (cvcpkg brokers
    the flow), so its lack of an RFC 8252 §7.3 port exemption is irrelevant.
    """
    try:
        p = urlsplit(uri)
    except ValueError:
        return False
    return (
        p.scheme == "http"
        and p.hostname in ("127.0.0.1", "::1")
        and p.path == "/callback"
        and not p.query
        and not p.fragment
        and p.port is not None
        and 1024 <= p.port <= 65535
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def verify_pkce_s256(verifier: str, challenge: str) -> bool:
    """True if *challenge* is the S256 transform of *verifier* (RFC 7636)."""
    if not verifier or not challenge:
        return False
    want = _b64url(hashlib.sha256(verifier.encode()).digest())
    return hmac.compare_digest(want, challenge)


def valid_code_challenge(challenge: str) -> bool:
    """A base64url S256 challenge is 43 unpadded chars."""
    return len(challenge) == 43 and all(c.isalnum() or c in "-_" for c in challenge)


def new_auth_code() -> str:
    return secrets.token_urlsafe(32)


def new_txn_id() -> str:
    return secrets.token_urlsafe(24)


def hash_auth_code(key: bytes, code: str) -> str:
    return hmac.new(derive_key(key, "authcode"), code.encode(), hashlib.sha256).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# ── Store ───────────────────────────────────────────────────────


class DbCliAuthStore:
    """Short-lived rows for the two CLI grants + brute-force accounting."""

    def __init__(self, hmac_key: bytes) -> None:
        self._key = hmac_key

    # -- device pairing --------------------------------------------------

    async def create_pairing(
        self,
        *,
        user_code: str,
        pairing_id: str,
        verifier_hash: str,
        device_label: str,
        client_version: str,
        platform: str,
        client_ip: str,
        requested_role: str,
        ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
        interval_seconds: int = 5,
    ) -> None:
        from cvcpkg.server.pairing import hash_pairing_id, hash_user_code

        now = _now()
        async with atomic_session() as session:
            session.add(
                CliPairingRow(
                    pairing_hash=hash_pairing_id(self._key, pairing_id),
                    user_code_hash=hash_user_code(self._key, user_code),
                    verifier_hash=verifier_hash,
                    status="pending",
                    requested_role=requested_role,
                    device_label=device_label[:128],
                    client_version=client_version[:32],
                    platform=platform[:64],
                    client_ip=client_ip[:64],
                    interval_seconds=interval_seconds,
                    created_at=now,
                    expires_at=now + datetime.timedelta(seconds=ttl_seconds),
                )
            )

    async def pairing_by_user_code(self, user_code: str) -> CliPairingRow | None:
        from cvcpkg.server.pairing import hash_user_code

        async with get_session() as session:
            return (
                (
                    await session.execute(
                        select(CliPairingRow).where(
                            CliPairingRow.user_code_hash == hash_user_code(self._key, user_code)
                        )
                    )
                )
                .scalars()
                .first()
            )

    async def pairing_by_id(self, pairing_id: str) -> CliPairingRow | None:
        from cvcpkg.server.pairing import hash_pairing_id

        async with get_session() as session:
            return (
                (
                    await session.execute(
                        select(CliPairingRow).where(
                            CliPairingRow.pairing_hash == hash_pairing_id(self._key, pairing_id)
                        )
                    )
                )
                .scalars()
                .first()
            )

    async def approve_pairing(self, pairing_row_id: int, principal_id: int, role: str) -> bool:
        async with atomic_session() as session:
            result = await session.execute(
                update(CliPairingRow)
                .where(CliPairingRow.id == pairing_row_id, CliPairingRow.status == "pending")
                .values(status="approved", principal_id=principal_id, granted_role=role)
            )
            return result.rowcount > 0

    async def deny_pairing(self, pairing_row_id: int) -> bool:
        async with atomic_session() as session:
            result = await session.execute(
                update(CliPairingRow)
                .where(CliPairingRow.id == pairing_row_id, CliPairingRow.status == "pending")
                .values(status="denied")
            )
            return result.rowcount > 0

    async def collect_pairing(self, pairing_row_id: int) -> bool:
        """Atomically flip approved -> collected exactly once (burn on collect)."""
        async with atomic_session() as session:
            result = await session.execute(
                update(CliPairingRow)
                .where(CliPairingRow.id == pairing_row_id, CliPairingRow.status == "approved")
                .values(status="collected")
            )
            return result.rowcount > 0

    async def note_poll(self, pairing_row_id: int) -> tuple[bool, int]:
        """Record a device poll; return ``(too_fast, interval_to_report)``.

        RFC 8628 §3.5: a poller faster than the current interval gets a
        ``slow_down`` and the interval doubles (capped); otherwise the poll is
        recorded and the steady interval returned.  The timestamp + interval
        live on the row so the backoff survives across stateless poll requests.
        """
        from cvcpkg.server import pairing as _pairing

        now = _now()
        async with atomic_session() as session:
            row = (
                (
                    await session.execute(
                        select(CliPairingRow).where(CliPairingRow.id == pairing_row_id)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False, _pairing.MIN_POLL_INTERVAL
            interval = max(int(row.interval_seconds or 0), _pairing.MIN_POLL_INTERVAL)
            last = _aware(row.last_poll_at)
            too_fast = last is not None and (now - last).total_seconds() < interval
            row.last_poll_at = now
            if too_fast:
                interval = _pairing.next_interval(interval, slow_down=True)
                row.interval_seconds = interval
                row.slow_down_strikes = int(row.slow_down_strikes or 0) + 1
            return too_fast, interval

    # -- loopback authorization codes -----------------------------------

    async def create_auth_code(
        self,
        *,
        code: str,
        principal_id: int,
        role: str,
        code_challenge: str,
        redirect_uri: str,
        device_label: str,
    ) -> None:
        now = _now()
        async with atomic_session() as session:
            session.add(
                CliAuthCodeRow(
                    code_hash=hash_auth_code(self._key, code),
                    principal_id=principal_id,
                    role=role,
                    code_challenge=code_challenge,
                    redirect_uri=redirect_uri,
                    device_label=device_label[:128],
                    used=False,
                    expires_at=now + datetime.timedelta(seconds=AUTH_CODE_TTL_SECONDS),
                )
            )

    async def redeem_auth_code(self, code: str) -> CliAuthCodeRow | None:
        """Atomically burn an unused, unexpired code and return its row.

        The single-use flip is a conditional UPDATE with rowcount 1, so a
        replayed code loses the race and gets nothing.
        """
        now = _now()
        code_hash = hash_auth_code(self._key, code)
        async with atomic_session() as session:
            row = (
                (
                    await session.execute(
                        select(CliAuthCodeRow).where(CliAuthCodeRow.code_hash == code_hash)
                    )
                )
                .scalars()
                .first()
            )
            if row is None or row.used:
                return None
            if _aware(row.expires_at) is not None and _aware(row.expires_at) < now:
                return None
            result = await session.execute(
                update(CliAuthCodeRow)
                .where(CliAuthCodeRow.id == row.id, CliAuthCodeRow.used == False)  # noqa: E712
                .values(used=True)
            )
            if result.rowcount != 1:
                return None
            return row

    # -- parked login transactions (authorize -> IdP -> resume) ---------

    async def create_login_txn(
        self,
        *,
        txn_id: str,
        client_id: str,
        redirect_uri: str,
        cli_state: str,
        code_challenge: str,
        requested_role: str,
        device_label: str,
    ) -> None:
        now = _now()
        async with atomic_session() as session:
            session.add(
                CliLoginTxnRow(
                    txn_id=txn_id,
                    client_id=client_id[:64],
                    redirect_uri=redirect_uri,
                    cli_state=cli_state,
                    code_challenge=code_challenge,
                    requested_role=requested_role,
                    device_label=device_label[:128],
                    expires_at=now + datetime.timedelta(seconds=LOGIN_TXN_TTL_SECONDS),
                )
            )

    async def take_login_txn(self, txn_id: str) -> CliLoginTxnRow | None:
        """Fetch-and-delete a parked transaction, single-use under concurrency.

        The DELETE is the gate: two concurrent resumes (double-click/refresh)
        both SELECT the row, but only the DELETE that reports rowcount 1 wins and
        returns it; the loser gets None instead of minting a second auth code.
        """
        now = _now()
        async with atomic_session() as session:
            row = (
                (
                    await session.execute(
                        select(CliLoginTxnRow).where(CliLoginTxnRow.txn_id == txn_id)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            expires_at = _aware(row.expires_at)
            # Detach a copy of the fields before deletion.
            detached = CliLoginTxnRow(
                txn_id=row.txn_id,
                client_id=row.client_id,
                redirect_uri=row.redirect_uri,
                cli_state=row.cli_state,
                code_challenge=row.code_challenge,
                requested_role=row.requested_role,
                device_label=row.device_label,
                expires_at=row.expires_at,
            )
            result = await session.execute(
                sa_delete(CliLoginTxnRow).where(CliLoginTxnRow.id == row.id)
            )
            if result.rowcount != 1:
                return None
            if expires_at is not None and expires_at < now:
                return None
            return detached

    # -- brute-force accounting -----------------------------------------

    async def is_locked_out(self, client_ip: str) -> bool:
        now = _now()
        async with get_session() as session:
            row = (
                (
                    await session.execute(
                        select(CodeAttemptRow).where(CodeAttemptRow.client_ip == client_ip)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            locked = _aware(row.locked_until)
            return locked is not None and locked > now

    async def record_failure(self, client_ip: str) -> None:
        """Count one bad user-code attempt, atomically.

        Uses DB-side UPDATEs rather than a read-modify-write so N parallel wrong
        guesses advance the counter by N (no lost updates), and converts the
        concurrent-first-failure PK race into an increment retry instead of a 500.
        """
        now = _now()
        ip = client_ip[:64]
        cutoff = now - datetime.timedelta(seconds=CODE_ATTEMPT_WINDOW_SECONDS)
        lock_until = now + datetime.timedelta(seconds=CODE_ATTEMPT_WINDOW_SECONDS)
        async with atomic_session() as session:
            # Atomic increment for a row whose window is still live.
            live = await session.execute(
                update(CodeAttemptRow)
                .where(CodeAttemptRow.client_ip == ip, CodeAttemptRow.window_start > cutoff)
                .values(failures=CodeAttemptRow.failures + 1)
            )
            if live.rowcount:
                await session.execute(
                    update(CodeAttemptRow)
                    .where(
                        CodeAttemptRow.client_ip == ip,
                        CodeAttemptRow.failures >= MAX_CODE_FAILURES,
                        CodeAttemptRow.locked_until.is_(None),
                    )
                    .values(locked_until=lock_until)
                )
                return
            # Window stale: reset it in place.
            reset = await session.execute(
                update(CodeAttemptRow)
                .where(CodeAttemptRow.client_ip == ip)
                .values(window_start=now, failures=1, locked_until=None)
            )
            if reset.rowcount:
                return
            # No row at all — insert; a concurrent insert loses the PK race, so
            # convert that into an increment retry rather than a 500.
            try:
                session.add(CodeAttemptRow(client_ip=ip, window_start=now, failures=1))
                await session.flush()
            except IntegrityError:
                await session.rollback()
                await session.execute(
                    update(CodeAttemptRow)
                    .where(CodeAttemptRow.client_ip == ip)
                    .values(failures=CodeAttemptRow.failures + 1)
                )

    async def clear_failures(self, client_ip: str) -> None:
        async with atomic_session() as session:
            await session.execute(
                sa_delete(CodeAttemptRow).where(CodeAttemptRow.client_ip == client_ip)
            )

    async def reap_expired(self) -> int:
        """Delete expired/consumed CLI-auth rows so the tables stay bounded.

        Abandoned pairings, used or expired auth codes, never-resumed login
        transactions, and stale brute-force counters accumulate otherwise; the
        ``expires_at`` indexes exist precisely for this sweep.
        """
        now = _now()
        cutoff = now - datetime.timedelta(seconds=CODE_ATTEMPT_WINDOW_SECONDS)
        removed = 0
        async with atomic_session() as session:
            for stmt in (
                sa_delete(CliPairingRow).where(CliPairingRow.expires_at < now),
                sa_delete(CliAuthCodeRow).where(
                    (CliAuthCodeRow.expires_at < now) | (CliAuthCodeRow.used == True)  # noqa: E712
                ),
                sa_delete(CliLoginTxnRow).where(CliLoginTxnRow.expires_at < now),
                sa_delete(CodeAttemptRow).where(
                    CodeAttemptRow.window_start < cutoff,
                    (CodeAttemptRow.locked_until.is_(None)) | (CodeAttemptRow.locked_until < now),
                ),
            ):
                result = await session.execute(stmt)
                removed += int(result.rowcount or 0)
        return removed
