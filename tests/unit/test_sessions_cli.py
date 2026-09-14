# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Store-level tests for CLI sessions: mint/verify, role clamp, refresh, GC.

These exercise DbSessionStore directly (no HTTP), where the security-critical
substitutions actually happen: a cvcses_ resolves to its principal, role is
clamped to the live entitlement AND the CLI ceiling, a disabled principal is
refused, refresh rotation is single-use with reuse->family revoke, and stale
rows are reaped.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from cvcpkg.server.models import TokenRole


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sess.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.delenv("CVCPKG_CLI_MAX_ROLE", raising=False)

    from cvcpkg.server.db import create_tables, init_db
    from cvcpkg.server.identities import DbPrincipalStore
    from cvcpkg.server.sessions import DbSessionStore

    async def _mk():
        init_db(db_url)
        await create_tables()

    asyncio.run(_mk())
    yield {
        "sessions": DbSessionStore(b"k" * 32),
        "principals": DbPrincipalStore(),
    }


def _run(coro):
    return asyncio.run(coro)


TTL = dict(ttl_seconds=3600, refresh_ttl_seconds=86400, max_lifetime_seconds=604800)


def _mk_principal(principals, name="joe", role="publisher"):
    return principals.upsert_from_claims(
        "https://idp.test", f"sub-{name}", {"preferred_username": name, "sub": f"sub-{name}"}, role
    )


class TestVerifyBearer:
    def test_session_resolves_to_its_principal(self, stores):
        async def body():
            p, _ = await _mk_principal(stores["principals"])
            m = await stores["sessions"].mint_cli(p, "publisher", **TTL)
            rec = await stores["sessions"].verify_bearer(m.access)
            assert rec is not None
            assert rec.name == "joe"
            assert rec.credential_kind == "session"
            assert rec.session_id == m.session_id
            assert rec.role == TokenRole.publisher

        _run(body())

    def test_role_clamped_to_current_entitlement(self, stores):
        async def body():
            p, _ = await _mk_principal(stores["principals"], role="publisher")
            # Mint an admin-labelled session, then the principal is demoted.
            m = await stores["sessions"].mint_cli(p, "admin", **TTL)
            await stores["principals"].upsert_from_claims(
                "https://idp.test",
                "sub-joe",
                {"preferred_username": "joe", "sub": "sub-joe"},
                "reader",
            )
            rec = await stores["sessions"].verify_bearer(m.access)
            assert rec.role == TokenRole.reader  # demotion takes effect immediately

        _run(body())

    def test_cli_max_role_ceiling_applied_at_verify(self, stores, monkeypatch):
        async def body():
            p, _ = await _mk_principal(stores["principals"], role="admin")
            m = await stores["sessions"].mint_cli(p, "admin", **TTL)
            monkeypatch.setenv("CVCPKG_CLI_MAX_ROLE", "reader")
            rec = await stores["sessions"].verify_bearer(m.access)
            assert rec.role == TokenRole.reader  # lowering the knob bounds live sessions

        _run(body())

    def test_disabled_principal_refused(self, stores):
        async def body():
            p, _ = await _mk_principal(stores["principals"])
            m = await stores["sessions"].mint_cli(p, "publisher", **TTL)
            await stores["principals"].set_disabled("joe", True)
            assert await stores["sessions"].verify_bearer(m.access) is None

        _run(body())

    def test_non_session_token_ignored(self, stores):
        async def body():
            assert await stores["sessions"].verify_bearer("cvctok_whatever") is None
            assert await stores["sessions"].verify_bearer("") is None

        _run(body())


class TestRotation:
    def test_rotate_then_reuse_revokes_family(self, stores):
        async def body():
            s = stores["sessions"]
            p, _ = await _mk_principal(stores["principals"])
            m = await s.mint_cli(p, "publisher", **TTL)
            r1 = await s.rotate(m.refresh, ttl_seconds=3600, refresh_ttl_seconds=86400)
            assert r1 is not None and r1.refresh != m.refresh
            # Reuse the burned refresh -> whole family revoked.
            assert await s.rotate(m.refresh, ttl_seconds=3600, refresh_ttl_seconds=86400) is None
            # ...successor is now dead too.
            assert await s.rotate(r1.refresh, ttl_seconds=3600, refresh_ttl_seconds=86400) is None

        _run(body())


class TestGc:
    def test_expire_stale_removes_revoked(self, stores):
        async def body():
            s = stores["sessions"]
            p, _ = await _mk_principal(stores["principals"])
            m = await s.mint_cli(p, "publisher", **TTL)
            await s.revoke(m.session_id)
            removed = await s.expire_stale()
            assert removed >= 1
            assert await s.verify_bearer(m.access) is None

        _run(body())
