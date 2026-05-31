"""Tests for the remote builder feature — DB store and REST endpoints."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for builder tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import AuditAction, BuilderStatus, TokenRole

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin and publisher tokens."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


# ── DbBuilderStore unit tests ──────────────────────────────────


class TestDbBuilderStore:
    """Direct tests for the DbBuilderStore class."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "builder_store.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield

        async def _cleanup():
            await dispose_engine()

        asyncio.run(_cleanup())

    def _run(self, coro):
        return asyncio.run(coro)

    def test_register_and_get(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            info = await store.register(
                name="builder-1",
                platform="linux",
                arch="x86_64",
                registered_by="test-admin",
                labels=["fast", "gpu"],
                capabilities={"cmake": True},
            )
            assert info.name == "builder-1"
            assert info.platform == "linux"
            assert info.arch == "x86_64"
            assert info.status == BuilderStatus.online
            assert info.labels == ["fast", "gpu"]
            assert info.capabilities == {"cmake": True}
            assert info.registered_by == "test-admin"

            fetched = await store.get(info.id)
            assert fetched is not None
            assert fetched.name == "builder-1"

        self._run(_test())

    def test_register_re_registration_updates(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            info1 = await store.register(
                name="builder-1", platform="linux", arch="x86_64",
                registered_by="admin-1", labels=["old"],
            )
            info2 = await store.register(
                name="builder-1", platform="linux", arch="arm64",
                registered_by="admin-2", labels=["new"],
            )
            assert info2.id == info1.id
            assert info2.arch == "arm64"
            assert info2.labels == ["new"]
            assert info2.registered_by == "admin-2"

        self._run(_test())

    def test_list_builders_with_filters(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            await store.register(name="b1", platform="linux", arch="x86_64", registered_by="a")
            await store.register(name="b2", platform="macos", arch="arm64", registered_by="a")
            await store.register(
                name="b3", platform="linux", arch="arm64",
                registered_by="a", org_slug="myorg",
            )

            all_builders = await store.list_builders()
            assert len(all_builders) == 3

            linux_only = await store.list_builders(platform="linux")
            assert len(linux_only) == 2

            arm64_only = await store.list_builders(arch="arm64")
            assert len(arm64_only) == 2

            org_only = await store.list_builders(org_slug="myorg")
            assert len(org_only) == 1
            assert org_only[0].name == "b3"

        self._run(_test())

    def test_update(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            info = await store.register(
                name="b1", platform="linux", arch="x86_64",
                registered_by="a", max_jobs=1,
            )

            updated = await store.update(info.id, max_jobs=4, labels=["updated"])
            assert updated is not None
            assert updated.max_jobs == 4
            assert updated.labels == ["updated"]

            # Original fields unchanged
            assert updated.platform == "linux"

        self._run(_test())

    def test_update_not_found(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            result = await store.update(9999, max_jobs=2)
            assert result is None

        self._run(_test())

    def test_heartbeat(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            info = await store.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            updated = await store.heartbeat(info.id, status="busy", current_jobs=2)
            assert updated is not None
            assert updated.status == "busy"
            assert updated.current_jobs == 2
            assert updated.last_heartbeat is not None

        self._run(_test())

    def test_heartbeat_not_found(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            result = await store.heartbeat(9999)
            assert result is None

        self._run(_test())

    def test_unregister(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            info = await store.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            removed = await store.unregister(info.id)
            assert removed is True

            fetched = await store.get(info.id)
            assert fetched is None

        self._run(_test())

    def test_unregister_not_found(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            removed = await store.unregister(9999)
            assert removed is False

        self._run(_test())

    def test_reap_stale(self):
        import datetime

        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            info = await store.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            assert info.status == BuilderStatus.online

            # Force last_heartbeat to be old
            from cvcpkg.server.db import BuilderRow, get_session
            from sqlalchemy import select

            async with get_session() as session:
                row = (
                    await session.execute(
                        select(BuilderRow).where(BuilderRow.id == info.id)
                    )
                ).scalar()
                row.last_heartbeat = datetime.datetime.now(
                    datetime.timezone.utc
                ) - datetime.timedelta(seconds=300)

            reaped = await store.reap_stale(max_age_seconds=180)
            assert len(reaped) == 1
            assert reaped[0].status == BuilderStatus.offline

        self._run(_test())

    def test_get_not_found(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _test():
            store = DbBuilderStore()
            result = await store.get(9999)
            assert result is None

        self._run(_test())


# ── API endpoint tests ──────────────────────────────────────────


class TestBuilderEndpoints:
    """Test builder REST endpoints via TestClient."""

    def _register(self, client, token, name="test-builder", platform="linux", arch="x86_64"):
        return client.post(
            "/v1/builders/register",
            json={
                "name": name,
                "platform": platform,
                "arch": arch,
                "max_jobs": 2,
                "labels": ["ci"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_register_builder(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = self._register(client, pub_tok)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "test-builder"
        assert data["platform"] == "linux"
        assert data["arch"] == "x86_64"
        assert data["status"] == "online"
        assert data["max_jobs"] == 2
        assert data["labels"] == ["ci"]

    def test_register_requires_auth(self, db_server_env):
        client, *_ = db_server_env
        resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
        )
        assert resp.status_code == 401

    def test_list_builders(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        self._register(client, pub_tok, "b1", "linux", "x86_64")
        self._register(client, pub_tok, "b2", "macos", "arm64")

        resp = client.get(
            "/v1/builders",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_list_builders_with_filter(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        self._register(client, pub_tok, "b1", "linux", "x86_64")
        self._register(client, pub_tok, "b2", "macos", "arm64")

        resp = client.get(
            "/v1/builders",
            params={"platform": "linux"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["builders"][0]["platform"] == "linux"

    def test_get_builder(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        reg = self._register(client, pub_tok)
        builder_id = reg.json()["id"]

        resp = client.get(
            f"/v1/builders/{builder_id}",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == builder_id

    def test_get_builder_not_found(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.get(
            "/v1/builders/9999",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_update_builder(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        reg = self._register(client, pub_tok)
        builder_id = reg.json()["id"]

        resp = client.patch(
            f"/v1/builders/{builder_id}",
            json={"max_jobs": 8, "labels": ["updated"]},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_jobs"] == 8
        assert data["labels"] == ["updated"]

    def test_heartbeat(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        reg = self._register(client, pub_tok)
        builder_id = reg.json()["id"]

        resp = client.post(
            f"/v1/builders/{builder_id}/heartbeat",
            json={"status": "busy", "current_jobs": 1},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "busy"
        assert data["current_jobs"] == 1

    def test_heartbeat_not_found(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builders/9999/heartbeat",
            json={"status": "online"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_unregister_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        reg = self._register(client, pub_tok)
        builder_id = reg.json()["id"]

        # Publisher should be denied
        resp = client.delete(
            f"/v1/builders/{builder_id}",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_unregister_admin(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        reg = self._register(client, pub_tok)
        builder_id = reg.json()["id"]

        resp = client.delete(
            f"/v1/builders/{builder_id}",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "builder unregistered"

        # Verify gone
        resp = client.get(
            f"/v1/builders/{builder_id}",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_unregister_not_found(self, db_server_env):
        client, admin_tok, *_ = db_server_env
        resp = client.delete(
            "/v1/builders/9999",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404

    def test_re_register_updates_existing(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp1 = self._register(client, pub_tok, "b1", "linux", "x86_64")
        id1 = resp1.json()["id"]

        resp2 = self._register(client, pub_tok, "b1", "linux", "arm64")
        id2 = resp2.json()["id"]

        # Same ID, updated arch
        assert id1 == id2
        assert resp2.json()["arch"] == "arm64"

    def test_empty_builder_list(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.get(
            "/v1/builders",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["builders"] == []
