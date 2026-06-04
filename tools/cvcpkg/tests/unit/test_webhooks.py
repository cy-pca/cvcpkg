"""Tests for Phase 5 — Webhook management (store and endpoints)."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for webhook tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin and reader tokens."""
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
        reader_raw = await store.create("test-reader", TokenRole.reader)
        await dispose_engine()
        return admin_raw, pub_raw, reader_raw

    admin_token, pub_token, reader_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, reader_token, tmp_path


# ── DbWebhookStore unit tests ──────────────────────────────────


class TestDbWebhookStore:
    """Direct tests for the DbWebhookStore class."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "webhook_store.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        self._tmp = tmp_path
        yield

        async def _cleanup():
            await dispose_engine()

        asyncio.run(_cleanup())

    def _run(self, coro):
        return asyncio.run(coro)

    def test_register_and_get(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register(
                url="https://example.com/hook",
                events=["build.completed", "package.published"],
                registered_by="admin1",
            )
            assert info.id > 0
            assert info.url == "https://example.com/hook"
            assert info.events == ["build.completed", "package.published"]
            assert info.active is True
            assert info.registered_by == "admin1"
            assert info.consecutive_failures == 0

            fetched = await store.get(info.id)
            assert fetched is not None
            assert fetched.url == info.url

        self._run(_test())

    def test_get_not_found(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            assert await store.get(9999) is None

        self._run(_test())

    def test_get_secret(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register(
                url="https://example.com/hook",
                events=["build.completed"],
                registered_by="admin1",
                secret="my-secret-key",
            )
            secret = await store.get_secret(info.id)
            assert secret == "my-secret-key"

        self._run(_test())

    def test_get_secret_not_found(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            assert await store.get_secret(9999) is None

        self._run(_test())

    def test_list_webhooks(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            await store.register("https://a.com/hook", ["e1"], "u1")
            await store.register("https://b.com/hook", ["e2"], "u2")
            hooks, total = await store.list_webhooks()
            assert total == 2
            assert len(hooks) == 2

        self._run(_test())

    def test_list_webhooks_org_filter(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            await store.register("https://a.com/hook", ["e1"], "u1", org_slug="org-a")
            await store.register("https://b.com/hook", ["e2"], "u2", org_slug="org-b")
            hooks, total = await store.list_webhooks(org_slug="org-a")
            assert total == 1
            assert hooks[0].url == "https://a.com/hook"

        self._run(_test())

    def test_list_webhooks_active_only(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            wh1 = await store.register("https://a.com/hook", ["e1"], "u1")
            await store.register("https://b.com/hook", ["e2"], "u2")
            await store.update(wh1.id, active=False)
            hooks, total = await store.list_webhooks(active_only=True)
            assert total == 1
            assert hooks[0].url == "https://b.com/hook"

        self._run(_test())

    def test_list_webhooks_pagination(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            for i in range(5):
                await store.register(f"https://h{i}.com/hook", ["e"], "u")
            hooks, total = await store.list_webhooks(limit=2, offset=0)
            assert total == 5
            assert len(hooks) == 2
            hooks2, _ = await store.list_webhooks(limit=2, offset=2)
            assert len(hooks2) == 2

        self._run(_test())

    def test_update(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register("https://old.com/hook", ["e1"], "u1")
            updated = await store.update(
                info.id,
                url="https://new.com/hook",
                events=["e2", "e3"],
            )
            assert updated is not None
            assert updated.url == "https://new.com/hook"
            assert updated.events == ["e2", "e3"]

        self._run(_test())

    def test_update_not_found(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            assert await store.update(9999, url="x") is None

        self._run(_test())

    def test_update_active_flag(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register("https://a.com/hook", ["e"], "u")
            assert info.active is True
            updated = await store.update(info.id, active=False)
            assert updated is not None
            assert updated.active is False

        self._run(_test())

    def test_delete(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register("https://a.com/hook", ["e"], "u")
            assert await store.delete(info.id) is True
            assert await store.get(info.id) is None

        self._run(_test())

    def test_delete_not_found(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            assert await store.delete(9999) is False

        self._run(_test())

    def test_record_delivery(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register("https://a.com/hook", ["e"], "u")
            # Simulate some failures first
            await store.record_failure(info.id)
            await store.record_failure(info.id)
            mid = await store.get(info.id)
            assert mid is not None
            assert mid.consecutive_failures == 2
            # Record delivery resets failures
            await store.record_delivery(info.id)
            after = await store.get(info.id)
            assert after is not None
            assert after.consecutive_failures == 0
            assert after.last_delivery_at is not None

        self._run(_test())

    def test_record_failure_auto_disable(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            info = await store.register("https://a.com/hook", ["e"], "u")
            # 4 failures — still active
            for _ in range(4):
                disabled = await store.record_failure(info.id)
                assert disabled is False
            # 5th failure should auto-disable
            disabled = await store.record_failure(info.id)
            assert disabled is True
            after = await store.get(info.id)
            assert after is not None
            assert after.active is False
            assert after.consecutive_failures == 5

        self._run(_test())

    def test_list_active_for_event(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            await store.register("https://a.com/hook", ["build.completed"], "u")
            await store.register("https://b.com/hook", ["package.published"], "u")
            await store.register("https://c.com/hook", ["*"], "u")
            # build.completed should match a and c
            result = await store.list_active_for_event("build.completed")
            urls = {r.url for r in result}
            assert "https://a.com/hook" in urls
            assert "https://c.com/hook" in urls
            assert "https://b.com/hook" not in urls

        self._run(_test())

    def test_list_active_for_event_org_scoped(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            await store.register("https://a.com/hook", ["e1"], "u", org_slug="org-a")
            await store.register("https://b.com/hook", ["e1"], "u", org_slug="org-b")
            result = await store.list_active_for_event("e1", org_slug="org-a")
            assert len(result) == 1
            assert result[0].url == "https://a.com/hook"

        self._run(_test())

    def test_list_active_excludes_inactive(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _test():
            store = DbWebhookStore()
            wh = await store.register("https://a.com/hook", ["e1"], "u")
            await store.update(wh.id, active=False)
            result = await store.list_active_for_event("e1")
            assert len(result) == 0

        self._run(_test())


# ── Endpoint tests ──────────────────────────────────────────────


class TestWebhookEndpoints:
    """Tests for the /v1/webhooks/* HTTP endpoints."""

    def _admin_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_register_webhook(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["build.completed"],
            },
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://example.com/hook"
        assert data["events"] == ["build.completed"]
        assert data["active"] is True
        assert data["id"] > 0

    def test_register_requires_admin(self, db_server_env):
        client, _, pub_token, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["build.completed"],
            },
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403

    def test_register_requires_auth(self, db_server_env):
        client, _, _, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["build.completed"],
            },
        )
        assert resp.status_code == 401

    def test_register_validation_url_required(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks",
            json={"events": ["e"]},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 422

    def test_register_validation_events_required(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks",
            json={"url": "https://example.com/hook", "events": []},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 422

    def test_list_webhooks_empty(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.get(
            "/v1/webhooks",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["webhooks"] == []

    def test_list_webhooks(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        client.post("/v1/webhooks", json={"url": "https://a.com/h", "events": ["e1"]}, headers=hdrs)
        client.post("/v1/webhooks", json={"url": "https://b.com/h", "events": ["e2"]}, headers=hdrs)
        resp = client.get("/v1/webhooks", headers=hdrs)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["webhooks"]) == 2

    def test_get_webhook(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        create_resp = client.post(
            "/v1/webhooks",
            json={"url": "https://a.com/h", "events": ["e1"]},
            headers=hdrs,
        )
        wh_id = create_resp.json()["id"]
        resp = client.get(f"/v1/webhooks/{wh_id}", headers=hdrs)
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://a.com/h"

    def test_get_webhook_not_found(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.get(
            "/v1/webhooks/9999",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 404

    def test_update_webhook(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        create_resp = client.post(
            "/v1/webhooks",
            json={"url": "https://old.com/h", "events": ["e1"]},
            headers=hdrs,
        )
        wh_id = create_resp.json()["id"]
        resp = client.patch(
            f"/v1/webhooks/{wh_id}",
            json={"url": "https://new.com/h", "events": ["e2", "e3"]},
            headers=hdrs,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://new.com/h"
        assert data["events"] == ["e2", "e3"]

    def test_update_webhook_not_found(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.patch(
            "/v1/webhooks/9999",
            json={"url": "https://new.com/h"},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 404

    def test_update_active_flag(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        create_resp = client.post(
            "/v1/webhooks",
            json={"url": "https://a.com/h", "events": ["e"]},
            headers=hdrs,
        )
        wh_id = create_resp.json()["id"]
        resp = client.patch(
            f"/v1/webhooks/{wh_id}",
            json={"active": False},
            headers=hdrs,
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_delete_webhook(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        create_resp = client.post(
            "/v1/webhooks",
            json={"url": "https://a.com/h", "events": ["e"]},
            headers=hdrs,
        )
        wh_id = create_resp.json()["id"]
        resp = client.delete(f"/v1/webhooks/{wh_id}", headers=hdrs)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify deleted
        resp2 = client.get(f"/v1/webhooks/{wh_id}", headers=hdrs)
        assert resp2.status_code == 404

    def test_delete_webhook_not_found(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.delete(
            "/v1/webhooks/9999",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 404

    def test_delete_requires_admin(self, db_server_env):
        client, _, pub_token, _, _ = db_server_env
        resp = client.delete(
            "/v1/webhooks/1",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403

    def test_list_requires_admin(self, db_server_env):
        client, _, pub_token, _, _ = db_server_env
        resp = client.get(
            "/v1/webhooks",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403

    def test_register_with_org_slug(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://a.com/h",
                "events": ["e1"],
                "org_slug": "my-org",
            },
            headers=hdrs,
        )
        assert resp.status_code == 200
        assert resp.json()["org_slug"] == "my-org"

    def test_list_filter_by_org(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)
        client.post(
            "/v1/webhooks",
            json={"url": "https://a.com/h", "events": ["e"], "org_slug": "org-a"},
            headers=hdrs,
        )
        client.post(
            "/v1/webhooks",
            json={"url": "https://b.com/h", "events": ["e"], "org_slug": "org-b"},
            headers=hdrs,
        )
        resp = client.get("/v1/webhooks", params={"org_slug": "org-a"}, headers=hdrs)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["webhooks"][0]["org_slug"] == "org-a"


# ── Webhook test-delivery endpoint tests ───────────────────────


class TestWebhookTestEndpoint:
    """Tests for POST /v1/webhooks/{id}/test."""

    @staticmethod
    def _admin_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _create_webhook(self, client, token: str) -> int:
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://httpbin.org/post",
                "events": ["webhook.test"],
            },
            headers=self._admin_headers(token),
        )
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_test_webhook_not_found(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks/9999/test",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 404

    def test_test_webhook_requires_admin(self, db_server_env):
        client, admin_token, pub_token, _, _ = db_server_env
        wh_id = self._create_webhook(client, admin_token)
        resp = client.post(
            f"/v1/webhooks/{wh_id}/test",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403

    def test_test_webhook_requires_auth(self, db_server_env):
        client, admin_token, _, _, _ = db_server_env
        wh_id = self._create_webhook(client, admin_token)
        resp = client.post(f"/v1/webhooks/{wh_id}/test")
        assert resp.status_code == 401
