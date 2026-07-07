"""Tests for webhook delivery engine and event emission."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
from unittest.mock import AsyncMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

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


# ── Delivery engine unit tests ──────────────────────────────────


class TestDeliverWebhook:
    """Tests for _deliver_webhook function."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "delivery.db"
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

    def test_successful_delivery(self):
        """Successful POST to webhook URL records delivery and returns True."""
        from cvcpkg.server.app import _deliver_webhook

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            # Patch app module's _db_webhooks
            import cvcpkg.server.app as app_mod

            old = app_mod._db_webhooks
            app_mod._db_webhooks = store

            try:
                wh = await store.register(
                    url="https://example.com/hook",
                    events=["build.completed"],
                    registered_by="admin",
                    secret="test-secret",
                )

                # Mock httpx.AsyncClient to simulate successful delivery
                mock_response = AsyncMock()
                mock_response.status_code = 200

                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post.return_value = mock_response
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    payload = json.dumps({"event": "build.completed", "data": {}})
                    result = await _deliver_webhook(
                        wh.id,
                        "https://example.com/hook",
                        "test-secret",
                        "build.completed",
                        payload,
                    )
                    assert result is True

                    # Verify POST was called with correct headers
                    call_args = mock_client.post.call_args
                    assert call_args[0][0] == "https://example.com/hook"
                    headers = call_args[1]["headers"]
                    assert headers["X-CvcPkg-Event"] == "build.completed"
                    assert headers["X-CvcPkg-Signature"].startswith("sha256=")
                    assert "X-CvcPkg-Delivery" in headers
                    assert headers["Content-Type"] == "application/json"

                # Verify HMAC signature is correct
                expected_sig = hmac.new(
                    b"test-secret",
                    payload.encode(),
                    hashlib.sha256,
                ).hexdigest()
                assert headers["X-CvcPkg-Signature"] == f"sha256={expected_sig}"

                # Check delivery was recorded (consecutive_failures reset)
                info = await store.get(wh.id)
                assert info.consecutive_failures == 0
            finally:
                app_mod._db_webhooks = old

        self._run(_test())

    def test_failed_delivery_http_error(self):
        """HTTP 500 response records failure and returns False."""
        from cvcpkg.server.app import _deliver_webhook

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old = app_mod._db_webhooks
            app_mod._db_webhooks = store

            try:
                wh = await store.register(
                    url="https://example.com/hook",
                    events=["e"],
                    registered_by="admin",
                )

                mock_response = AsyncMock()
                mock_response.status_code = 500

                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post.return_value = mock_response
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    result = await _deliver_webhook(
                        wh.id,
                        "https://example.com/hook",
                        "",
                        "e",
                        "{}",
                    )
                    assert result is False

                info = await store.get(wh.id)
                assert info.consecutive_failures == 1
            finally:
                app_mod._db_webhooks = old

        self._run(_test())

    def test_failed_delivery_network_error(self):
        """Network error records failure and returns False."""
        from cvcpkg.server.app import _deliver_webhook

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old = app_mod._db_webhooks
            app_mod._db_webhooks = store

            try:
                wh = await store.register(
                    url="https://example.com/hook",
                    events=["e"],
                    registered_by="admin",
                )

                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post.side_effect = ConnectionError("unreachable")
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    result = await _deliver_webhook(
                        wh.id,
                        "https://example.com/hook",
                        "",
                        "e",
                        "{}",
                    )
                    assert result is False

                info = await store.get(wh.id)
                assert info.consecutive_failures == 1
            finally:
                app_mod._db_webhooks = old

        self._run(_test())

    def test_signature_with_empty_secret(self):
        """Empty secret still produces valid HMAC signature."""
        from cvcpkg.server.app import _deliver_webhook

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old = app_mod._db_webhooks
            app_mod._db_webhooks = store

            try:
                wh = await store.register(
                    url="https://example.com/hook",
                    events=["e"],
                    registered_by="admin",
                )

                mock_response = AsyncMock()
                mock_response.status_code = 200

                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post.return_value = mock_response
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    payload = '{"event": "test"}'
                    result = await _deliver_webhook(
                        wh.id,
                        "https://example.com/hook",
                        "",
                        "e",
                        payload,
                    )
                    assert result is True

                    headers = mock_client.post.call_args[1]["headers"]
                    expected_sig = hmac.new(
                        b"",
                        payload.encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    assert headers["X-CvcPkg-Signature"] == f"sha256={expected_sig}"
            finally:
                app_mod._db_webhooks = old

        self._run(_test())


class TestEmitWebhookEvent:
    """Tests for emit_webhook_event — the main dispatcher."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "emit.db"
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

    def test_emit_no_db(self):
        """emit_webhook_event is a no-op when DB is not configured."""
        from cvcpkg.server.app import emit_webhook_event

        async def _test():
            import cvcpkg.server.app as app_mod

            old_use_db = app_mod._use_db
            app_mod._use_db = False
            try:
                # Should not raise
                await emit_webhook_event("build.completed", {"job_id": 1})
            finally:
                app_mod._use_db = old_use_db

        self._run(_test())

    def test_emit_no_matching_webhooks(self):
        """No active webhooks for event → no delivery tasks spawned."""
        from cvcpkg.server.app import emit_webhook_event

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old_use_db = app_mod._use_db
            old_wh = app_mod._db_webhooks
            app_mod._use_db = True
            app_mod._db_webhooks = store

            try:
                # Register webhook for different event
                await store.register(
                    "https://example.com/hook",
                    ["package.published"],
                    "admin",
                )

                with patch(
                    "cvcpkg.server.app._deliver_with_retries",
                    new_callable=AsyncMock,
                ) as mock_deliver:
                    await emit_webhook_event("build.completed", {"job_id": 1})
                    mock_deliver.assert_not_called()
            finally:
                app_mod._use_db = old_use_db
                app_mod._db_webhooks = old_wh

        self._run(_test())

    def test_emit_spawns_delivery_tasks(self):
        """Matching webhooks get background delivery tasks."""
        from cvcpkg.server.app import emit_webhook_event

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old_use_db = app_mod._use_db
            old_wh = app_mod._db_webhooks
            app_mod._use_db = True
            app_mod._db_webhooks = store

            try:
                await store.register(
                    "https://a.com/hook",
                    ["build.completed"],
                    "admin",
                )
                await store.register(
                    "https://b.com/hook",
                    ["build.completed"],
                    "admin",
                )
                await store.register(
                    "https://c.com/hook",
                    ["package.published"],
                    "admin",
                )

                with patch(
                    "cvcpkg.server.app._deliver_with_retries",
                    new_callable=AsyncMock,
                ) as mock_deliver:
                    await emit_webhook_event("build.completed", {"job_id": 1})
                    # Allow background tasks to be created
                    await asyncio.sleep(0.05)
                    # Should have been called for 2 matching webhooks (a and b)
                    assert mock_deliver.call_count == 2
            finally:
                app_mod._use_db = old_use_db
                app_mod._db_webhooks = old_wh

        self._run(_test())

    def test_emit_wildcard_webhook(self):
        """Webhook with events=["*"] matches all events."""
        from cvcpkg.server.app import emit_webhook_event

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old_use_db = app_mod._use_db
            old_wh = app_mod._db_webhooks
            app_mod._use_db = True
            app_mod._db_webhooks = store

            try:
                await store.register(
                    "https://all.com/hook",
                    ["*"],
                    "admin",
                )

                with patch(
                    "cvcpkg.server.app._deliver_with_retries",
                    new_callable=AsyncMock,
                ) as mock_deliver:
                    await emit_webhook_event("any.event", {"data": "test"})
                    await asyncio.sleep(0.05)
                    assert mock_deliver.call_count == 1
            finally:
                app_mod._use_db = old_use_db
                app_mod._db_webhooks = old_wh

        self._run(_test())

    def test_emit_org_scoped(self):
        """org_slug filtering is passed through to list_active_for_event."""
        from cvcpkg.server.app import emit_webhook_event

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old_use_db = app_mod._use_db
            old_wh = app_mod._db_webhooks
            app_mod._use_db = True
            app_mod._db_webhooks = store

            try:
                await store.register(
                    "https://a.com/hook",
                    ["e"],
                    "admin",
                    org_slug="org-a",
                )
                await store.register(
                    "https://b.com/hook",
                    ["e"],
                    "admin",
                    org_slug="org-b",
                )

                with patch(
                    "cvcpkg.server.app._deliver_with_retries",
                    new_callable=AsyncMock,
                ) as mock_deliver:
                    await emit_webhook_event("e", {}, org_slug="org-a")
                    await asyncio.sleep(0.05)
                    assert mock_deliver.call_count == 1
            finally:
                app_mod._use_db = old_use_db
                app_mod._db_webhooks = old_wh

        self._run(_test())

    def test_emit_payload_format(self):
        """Emitted payload contains event, timestamp, and data."""
        from cvcpkg.server.app import emit_webhook_event

        async def _test():
            from cvcpkg.server.db_stores import DbWebhookStore

            store = DbWebhookStore()
            import cvcpkg.server.app as app_mod

            old_use_db = app_mod._use_db
            old_wh = app_mod._db_webhooks
            app_mod._use_db = True
            app_mod._db_webhooks = store

            try:
                await store.register(
                    "https://example.com/hook",
                    ["e"],
                    "admin",
                )

                with patch(
                    "cvcpkg.server.app._deliver_with_retries",
                    new_callable=AsyncMock,
                ) as mock_deliver:
                    await emit_webhook_event("e", {"key": "value"})
                    await asyncio.sleep(0.05)
                    assert mock_deliver.call_count == 1
                    # Check the payload arg contains event, timestamp, data
                    call_args = mock_deliver.call_args
                    payload_str = call_args[0][4]  # 5th positional arg
                    payload_obj = json.loads(payload_str)
                    assert payload_obj["event"] == "e"
                    assert "timestamp" in payload_obj
                    assert payload_obj["data"] == {"key": "value"}
            finally:
                app_mod._use_db = old_use_db
                app_mod._db_webhooks = old_wh

        self._run(_test())


# ── Event emission integration tests ───────────────────────────


class TestWebhookEventEmission:
    """Integration tests verifying webhook events are emitted at key points."""

    def _admin_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_publish_emits_package_published(self, db_server_env):
        """Publishing a package emits package.published webhook event."""
        client, admin_token, pub_token, tmp_path = db_server_env
        hdrs = self._admin_headers(admin_token)

        # Register a webhook for package.published
        client.post(
            "/v1/webhooks",
            json={"url": "https://example.com/hook", "events": ["package.published"]},
            headers=hdrs,
        )

        archive = b"test archive content"
        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post(
                "/v1/publish",
                params={
                    "name": "testpkg",
                    "version": "1.0.0",
                    "platform": "linux",
                    "arch": "x86_64",
                },
                files={"file": ("testpkg.tar.zst", io.BytesIO(archive))},
                headers=self._admin_headers(pub_token),
            )
            assert resp.status_code == 200

            # Verify emit was called with package.published
            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "package.published"
            data = call_args[0][1]
            assert data["name"] == "testpkg"
            assert data["version"] == "1.0.0"

    def test_builder_register_emits_builder_online(self, db_server_env):
        """Registering a builder emits builder.online webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post(
                "/v1/builders/register",
                json={
                    "name": "builder-1",
                    "platform": "linux",
                    "arch": "x86_64",
                },
                headers=hdrs,
            )
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "builder.online"
            data = call_args[0][1]
            assert data["builder_name"] == "builder-1"

    def test_builder_unregister_emits_builder_offline(self, db_server_env):
        """Unregistering a builder emits builder.offline webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        # First register a builder
        resp = client.post(
            "/v1/builders/register",
            json={"name": "builder-1", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        builder_id = resp.json()["id"]

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.delete(f"/v1/builders/{builder_id}", headers=hdrs)
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "builder.offline"

    def test_build_claim_emits_build_started(self, db_server_env):
        """Claiming a build job emits build.started webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        # Register a builder
        resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        builder_id = resp.json()["id"]

        # Submit a job
        resp = client.post(
            "/v1/builds",
            json={"recipe_name": "pkg", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        job_id = resp.json()["id"]

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post(
                f"/v1/builds/{job_id}/claim",
                json={"builder_id": builder_id},
                headers=hdrs,
            )
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "build.started"
            data = call_args[0][1]
            assert data["job_id"] == job_id
            assert data["builder_id"] == builder_id

    def test_build_complete_emits_build_completed(self, db_server_env):
        """Completing a build emits build.completed webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        # Register builder + submit + claim
        resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        builder_id = resp.json()["id"]
        resp = client.post(
            "/v1/builds",
            json={"recipe_name": "pkg", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        job_id = resp.json()["id"]
        client.post(f"/v1/builds/{job_id}/claim", json={"builder_id": builder_id}, headers=hdrs)

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post(
                f"/v1/builds/{job_id}/complete",
                json={"result_archive_url": "/v1/download/result.tar.zst"},
                headers=hdrs,
            )
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "build.completed"
            data = call_args[0][1]
            assert data["job_id"] == job_id
            assert data["result_archive_url"] == "/v1/download/result.tar.zst"

    def test_build_fail_emits_build_failed(self, db_server_env):
        """Failing a build emits build.failed webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        builder_id = resp.json()["id"]
        resp = client.post(
            "/v1/builds",
            json={"recipe_name": "pkg", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        job_id = resp.json()["id"]
        client.post(f"/v1/builds/{job_id}/claim", json={"builder_id": builder_id}, headers=hdrs)

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post(
                f"/v1/builds/{job_id}/fail",
                json={"error_message": "compilation failed"},
                headers=hdrs,
            )
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "build.failed"
            data = call_args[0][1]
            assert data["job_id"] == job_id
            assert data["error_message"] == "compilation failed"

    def test_build_cancel_emits_build_cancelled(self, db_server_env):
        """Cancelling a build emits build.cancelled webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        resp = client.post(
            "/v1/builds",
            json={"recipe_name": "pkg", "platform": "linux", "arch": "x86_64"},
            headers=hdrs,
        )
        job_id = resp.json()["id"]

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post(f"/v1/builds/{job_id}/cancel", headers=hdrs)
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "build.cancelled"
            data = call_args[0][1]
            assert data["job_id"] == job_id

    def test_dag_cancel_emits_build_cancelled(self, db_server_env):
        """Cancelling a DAG emits build.cancelled webhook event."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        resp = client.post(
            "/v1/builds/dag",
            json={
                "dag_id": "test-dag",
                "jobs": [
                    {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                    {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": []},
                ],
            },
            headers=hdrs,
        )
        assert resp.status_code == 200

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as mock_emit:
            resp = client.post("/v1/builds/dag/test-dag/cancel", headers=hdrs)
            assert resp.status_code == 200

            mock_emit.assert_called()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "build.cancelled"
            data = call_args[0][1]
            assert data["dag_id"] == "test-dag"
            assert data["cancelled"] >= 2


# ── Webhook test endpoint ──────────────────────────────────────


class TestWebhookTestEndpoint:
    """Tests for POST /v1/webhooks/{id}/test."""

    def _admin_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_webhook_test_delivery(self, db_server_env):
        """Test endpoint sends a test payload to the webhook URL."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        # Register a webhook
        resp = client.post(
            "/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["build.completed"],
                "secret": "my-secret",
            },
            headers=hdrs,
        )
        wh_id = resp.json()["id"]

        # Mock httpx to avoid real HTTP call
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = client.post(f"/v1/webhooks/{wh_id}/test", headers=hdrs)
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["status_code"] == 200
            assert data["webhook_id"] == wh_id

            # Verify correct headers
            call_args = mock_client.post.call_args
            headers = call_args[1]["headers"]
            assert headers["X-CvcPkg-Event"] == "webhook.test"
            assert headers["X-CvcPkg-Signature"].startswith("sha256=")

    def test_webhook_test_not_found(self, db_server_env):
        client, admin_token, _, _ = db_server_env
        resp = client.post(
            "/v1/webhooks/9999/test",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 404

    def test_webhook_test_delivery_failure(self, db_server_env):
        """Delivery failure returns 502."""
        client, admin_token, _, _ = db_server_env
        hdrs = self._admin_headers(admin_token)

        resp = client.post(
            "/v1/webhooks",
            json={"url": "https://bad.com/hook", "events": ["e"]},
            headers=hdrs,
        )
        wh_id = resp.json()["id"]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = ConnectionError("unreachable")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            resp = client.post(f"/v1/webhooks/{wh_id}/test", headers=hdrs)
            assert resp.status_code == 502

    def test_webhook_test_requires_admin(self, db_server_env):
        client, _, pub_token, _ = db_server_env
        resp = client.post(
            "/v1/webhooks/1/test",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403
