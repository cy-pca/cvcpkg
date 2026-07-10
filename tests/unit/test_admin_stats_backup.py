"""Tests for the admin stats and backup endpoints (/v1/admin/stats, /v1/admin/backup)."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for these tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed (file sqlite) test server with admin and publisher tokens."""
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


# ── /v1/admin/stats ─────────────────────────────────────────────


class TestAdminStats:
    def test_stats_requires_admin(self, db_server_env):
        client, _admin, pub, _ = db_server_env
        r = client.get("/v1/admin/stats", headers={"Authorization": f"Bearer {pub}"})
        assert r.status_code == 403

    def test_stats_unauthenticated(self, db_server_env):
        client, _admin, _pub, _ = db_server_env
        r = client.get("/v1/admin/stats")
        assert r.status_code in (401, 403)

    def test_stats_shape(self, db_server_env):
        client, admin, _pub, _ = db_server_env
        r = client.get("/v1/admin/stats", headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200
        data = r.json()
        assert data["database_enabled"] is True
        assert data["database_backend"] == "sqlite"
        assert data["packages_count"] == 0
        assert data["total_storage_bytes"] == 0
        # Counts present and integer-typed.
        for key in ("orgs_count", "builders_count", "build_jobs_count", "audit_entries"):
            assert isinstance(data[key], int)
        assert "version" in data
        assert data["uptime_seconds"] >= 0


# ── /v1/admin/backup ────────────────────────────────────────────


class TestAdminBackup:
    def test_backup_requires_admin(self, db_server_env):
        client, _admin, pub, _ = db_server_env
        r = client.post("/v1/admin/backup", headers={"Authorization": f"Bearer {pub}"})
        assert r.status_code == 403

    def test_backup_creates_file(self, db_server_env):
        client, admin, _pub, tmp_path = db_server_env
        r = client.post("/v1/admin/backup", headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["backend"] == "sqlite"
        assert data["size_bytes"] > 0

        backups = list((tmp_path / "backups").glob("backup-*.sqlite"))
        assert len(backups) == 1
        assert backups[0].stat().st_size == data["size_bytes"]

    def test_backup_recorded_in_audit(self, db_server_env):
        client, admin, _pub, _ = db_server_env
        client.post("/v1/admin/backup", headers={"Authorization": f"Bearer {admin}"})
        r = client.get(
            "/v1/audit",
            params={"action": "backup"},
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert any(e["action"] == "backup" and e["target"] == "database" for e in entries)
