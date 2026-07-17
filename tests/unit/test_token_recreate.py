"""Reissuing a token name after revoke — DB / YAML backend parity.

The initial schema put a full column ``UNIQUE(name)`` on ``tokens``, but the
app-level guard (and the YAML ``TokenStore``) only forbid a second *active*
token of the same name.  So recreating a revoked name succeeded on YAML while
the DB backend raised an IntegrityError that escaped ``create()`` as HTTP 500.
Token rotation, which leaves revoked rows behind, makes the collision more
likely.

These lock in the fix: a partial unique index (``uq_tokens_active_name``, over
non-revoked rows) plus ``DbTokenStore.create()``'s IntegrityError→409 net.
"""

from __future__ import annotations

import asyncio
import io

import pytest

pytest.importorskip("pydantic", reason="server extras not installed")

from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole

# ── YAML backend ────────────────────────────────────────────────


class TestYamlTokenRecreate:
    def test_recreate_after_revoke_succeeds(self, tmp_path):
        store = TokenStore(tmp_path)
        old = store.create("ci-bot", TokenRole.publisher)
        store.revoke("ci-bot")
        new = store.create("ci-bot", TokenRole.publisher)
        assert new != old
        assert store.verify(new) is not None  # reissued secret works
        assert store.verify(old) is None  # old (revoked) secret is dead

    def test_second_active_name_rejected(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("ci-bot", TokenRole.publisher)
        with pytest.raises(ValueError):
            store.create("ci-bot", TokenRole.publisher)


# ── DB backend (SQLite) ─────────────────────────────────────────


class TestDbTokenRecreate:
    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")
        self.db_url = f"sqlite+aiosqlite:///{tmp_path / 'recreate.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", self.db_url)
        self.tmp_path = tmp_path

    def _run(self, coro_fn):
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _wrapped():
            init_db(self.db_url)
            await create_tables()
            store = DbTokenStore(self.tmp_path)
            try:
                return await coro_fn(store)
            finally:
                await dispose_engine()

        return asyncio.run(_wrapped())

    def test_recreate_after_revoke_succeeds(self):
        async def scenario(store):
            old = await store.create("ci-bot", TokenRole.publisher)
            await store.revoke("ci-bot")
            new = await store.create("ci-bot", TokenRole.publisher)
            assert new != old
            assert await store.verify(new) is not None
            assert await store.verify(old) is None
            # Both rows persist: exactly one active, one revoked.
            same_name = [r for r in await store.list_tokens() if r.name == "ci-bot"]
            assert len(same_name) == 2
            assert sum(1 for r in same_name if r.revoked) == 1

        self._run(scenario)

    def test_second_active_name_rejected_as_valueerror(self):
        # The active-name guard must still fire — and as a ValueError (→ 409),
        # never an IntegrityError (→ 500).
        async def scenario(store):
            await store.create("ci-bot", TokenRole.publisher)
            with pytest.raises(ValueError):
                await store.create("ci-bot", TokenRole.publisher)

        self._run(scenario)

    def test_repeated_revoke_recreate_cycles(self):
        async def scenario(store):
            seen = []
            for _ in range(3):
                raw = await store.create("cyc", TokenRole.publisher)
                seen.append(raw)
                assert await store.verify(raw) is not None
                await store.revoke("cyc")
                assert await store.verify(raw) is None
            live = await store.create("cyc", TokenRole.publisher)
            assert await store.verify(live) is not None
            assert len(set(seen)) == 3  # every cycle minted a distinct secret

        self._run(scenario)


# ── Backend parity ──────────────────────────────────────────────


def test_backend_parity_recreate_after_revoke(tmp_path, monkeypatch):
    """YAML and DB backends agree: a revoked name can be reissued."""
    yml = TokenStore(tmp_path / "yaml")
    yml.create("bot", TokenRole.publisher)
    yml.revoke("bot")
    assert yml.create("bot", TokenRole.publisher)  # does not raise

    pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'parity.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _db():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path / "db")
        await store.create("bot", TokenRole.publisher)
        await store.revoke("bot")
        raw = await store.create("bot", TokenRole.publisher)  # must not raise
        await dispose_engine()
        return raw

    assert asyncio.run(_db())


# ── REST endpoint (DB backend) ──────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    pytest.importorskip("aiosqlite", reason="aiosqlite required for DB tests")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("test-admin", TokenRole.admin)
        await dispose_engine()
        return admin

    admin_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token


class TestTokenRecreateAPI:
    def test_recreate_after_revoke_returns_200_not_500(self, db_server_env):
        client, admin = db_server_env
        h = {"Authorization": f"Bearer {admin}"}
        assert (
            client.post("/v1/tokens", json={"name": "ci-bot", "role": "publisher"}, headers=h)
        ).status_code == 200
        assert client.delete("/v1/tokens/ci-bot", headers=h).status_code == 200
        # Reissue the same name — this was a 500 (IntegrityError) before the fix.
        resp = client.post("/v1/tokens", json={"name": "ci-bot", "role": "publisher"}, headers=h)
        assert resp.status_code == 200, resp.text
        new_secret = resp.json()["token"]
        # The reissued secret authenticates.
        probe = client.post(
            "/v1/publish",
            params={"name": "pkg", "version": "1.0", "platform": "linux", "arch": "x86_64"},
            files={"file": ("p.tar.zst", io.BytesIO(b"probe"))},
            headers={"Authorization": f"Bearer {new_secret}"},
        )
        assert probe.status_code == 200, probe.text

    def test_duplicate_active_name_returns_409(self, db_server_env):
        client, admin = db_server_env
        h = {"Authorization": f"Bearer {admin}"}
        assert (
            client.post("/v1/tokens", json={"name": "dup", "role": "publisher"}, headers=h)
        ).status_code == 200
        resp = client.post("/v1/tokens", json={"name": "dup", "role": "publisher"}, headers=h)
        assert resp.status_code == 409, resp.text
