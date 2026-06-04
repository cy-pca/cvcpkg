"""Tests for Phase 6 — Retention & Quota management."""

from __future__ import annotations

import asyncio
import datetime

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for retention tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin token."""
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


# ── DbBuildJobStore purge/quota unit tests ──────────────────────


class TestRetentionStore:
    """Direct tests for purge and quota methods in DbBuildJobStore."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "retention_store.db"
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

    async def _create_job(
        self, store, *, finished_days_ago=None, status="completed", org_slug="", log_content=None
    ):
        """Helper to create a build job and optionally finish it."""
        info = await store.create(
            recipe_name="test-pkg",
            platform="linux",
            arch="x86_64",
            submitted_by="admin",
            recipe_version="1.0",
            recipe_hash="abc123",
            config="release",
            link="shared",
            org_slug=org_slug,
        )
        if finished_days_ago is not None:
            from sqlalchemy import select

            from cvcpkg.server.db import BuildJobRow, get_session

            finished = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                days=finished_days_ago
            )
            async with get_session() as session:
                row = (
                    await session.execute(select(BuildJobRow).where(BuildJobRow.id == info.id))
                ).scalar()
                row.status = status
                row.finished_at = finished
                if log_content is not None:
                    logs_dir = self._tmp / "logs"
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    log_file = logs_dir / f"job-{info.id}.log"
                    log_file.write_text(log_content)
                    row.log_url = f"job-{info.id}.log"
                    row.log_size_bytes = len(log_content)
        return info

    def test_purge_old_logs_basic(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            logs_dir = self._tmp / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)

            # Create old finished job with log
            info = await self._create_job(store, finished_days_ago=60, log_content="old log output")
            log_path = logs_dir / f"job-{info.id}.log"
            assert log_path.is_file()

            purged = await store.purge_old_logs(
                older_than_days=30,
                logs_dir=logs_dir,
            )
            assert purged == 1
            assert not log_path.is_file()

        self._run(_test())

    def test_purge_old_logs_skips_recent(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            logs_dir = self._tmp / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)

            # Recent job (5 days old)
            await self._create_job(store, finished_days_ago=5, log_content="recent log")
            purged = await store.purge_old_logs(
                older_than_days=30,
                logs_dir=logs_dir,
            )
            assert purged == 0

        self._run(_test())

    def test_purge_old_logs_status_filter(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            logs_dir = self._tmp / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)

            # Old completed job
            await self._create_job(
                store,
                finished_days_ago=60,
                status="completed",
                log_content="completed log",
            )
            # Old failed job
            await self._create_job(
                store,
                finished_days_ago=60,
                status="failed",
                log_content="failed log",
            )

            # Only purge failed
            purged = await store.purge_old_logs(
                older_than_days=30,
                logs_dir=logs_dir,
                status_filter="failed",
            )
            assert purged == 1

        self._run(_test())

    def test_purge_old_logs_no_delete(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            logs_dir = self._tmp / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)

            info = await self._create_job(store, finished_days_ago=60, log_content="keep file")
            log_path = logs_dir / f"job-{info.id}.log"
            assert log_path.is_file()

            purged = await store.purge_old_logs(
                older_than_days=30,
                logs_dir=logs_dir,
                delete_logs=False,
            )
            assert purged == 1
            # File should still exist when delete_logs=False
            assert log_path.is_file()

        self._run(_test())

    def test_get_org_log_usage(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await self._create_job(
                store,
                finished_days_ago=5,
                org_slug="org-a",
                log_content="x" * 100,
            )
            await self._create_job(
                store,
                finished_days_ago=5,
                org_slug="org-a",
                log_content="y" * 200,
            )
            await self._create_job(
                store,
                finished_days_ago=5,
                org_slug="org-b",
                log_content="z" * 50,
            )

            usage_a = await store.get_org_log_usage("org-a")
            assert usage_a == 300
            usage_b = await store.get_org_log_usage("org-b")
            assert usage_b == 50
            usage_c = await store.get_org_log_usage("org-c")
            assert usage_c == 0

        self._run(_test())

    def test_purge_old_jobs(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            logs_dir = self._tmp / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)

            info = await self._create_job(store, finished_days_ago=60, log_content="old job")
            # Verify job exists
            job = await store.get(info.id)
            assert job is not None

            purged = await store.purge_old_jobs(
                older_than_days=30,
                logs_dir=logs_dir,
            )
            assert purged == 1
            # Job row should be deleted
            job = await store.get(info.id)
            assert job is None

        self._run(_test())

    def test_purge_old_jobs_skips_recent(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            logs_dir = self._tmp / "logs"

            info = await self._create_job(store, finished_days_ago=5)
            purged = await store.purge_old_jobs(
                older_than_days=30,
                logs_dir=logs_dir,
            )
            assert purged == 0
            job = await store.get(info.id)
            assert job is not None

        self._run(_test())


# ── Endpoint tests ──────────────────────────────────────────────


class TestRetentionEndpoints:
    """Tests for the /v1/admin/gc/logs and /v1/admin/purge/builds endpoints."""

    def _admin_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _create_old_job(self, client, admin_token, tmp_path, *, days_ago=60):
        """Submit a job and manually age it via direct DB manipulation."""
        hdrs = self._admin_headers(admin_token)
        # Submit a job
        resp = client.post(
            "/v1/builds",
            json={
                "recipe_name": "test-pkg",
                "platform": "linux",
                "arch": "x86_64",
            },
            headers=hdrs,
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        # Age the job directly in the DB
        import asyncio

        async def _age():
            from sqlalchemy import select

            from cvcpkg.server.db import BuildJobRow, get_session

            finished = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                days=days_ago
            )
            async with get_session() as session:
                row = (
                    await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
                ).scalar()
                row.status = "completed"
                row.finished_at = finished
                # Create a log file
                logs_dir = tmp_path / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file = logs_dir / f"job-{job_id}.log"
                log_file.write_text(f"log for job {job_id}")
                row.log_url = f"job-{job_id}.log"
                row.log_size_bytes = log_file.stat().st_size

        asyncio.run(_age())
        return job_id

    def test_gc_logs(self, db_server_env):
        client, admin_token, _, tmp_path = db_server_env
        self._create_old_job(client, admin_token, tmp_path, days_ago=60)
        resp = client.post(
            "/v1/admin/gc/logs",
            params={"older_than_days": 30},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["purged"] >= 1

    def test_gc_logs_requires_admin(self, db_server_env):
        client, _, pub_token, _ = db_server_env
        resp = client.post(
            "/v1/admin/gc/logs",
            params={"older_than_days": 30},
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403

    def test_gc_logs_nothing_to_purge(self, db_server_env):
        client, admin_token, _, _ = db_server_env
        resp = client.post(
            "/v1/admin/gc/logs",
            params={"older_than_days": 30},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["purged"] == 0

    def test_gc_logs_status_filter(self, db_server_env):
        client, admin_token, _, tmp_path = db_server_env
        self._create_old_job(client, admin_token, tmp_path, days_ago=60)
        resp = client.post(
            "/v1/admin/gc/logs",
            params={"older_than_days": 30, "status": "failed"},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        # Status filter is "failed" but job is "completed", so no purge
        assert resp.json()["purged"] == 0

    def test_purge_builds(self, db_server_env):
        client, admin_token, _, tmp_path = db_server_env
        job_id = self._create_old_job(client, admin_token, tmp_path, days_ago=60)
        resp = client.post(
            "/v1/admin/purge/builds",
            params={"older_than_days": 30},
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["purged"] >= 1
        # Job should be gone
        resp2 = client.get(
            f"/v1/builds/{job_id}",
            headers=self._admin_headers(admin_token),
        )
        assert resp2.status_code == 404

    def test_purge_builds_requires_admin(self, db_server_env):
        client, _, pub_token, _ = db_server_env
        resp = client.post(
            "/v1/admin/purge/builds",
            params={"older_than_days": 30},
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403

    def test_org_log_usage(self, db_server_env):
        client, admin_token, _, _ = db_server_env
        resp = client.get(
            "/v1/admin/quota/logs/test-org",
            headers=self._admin_headers(admin_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["org_slug"] == "test-org"
        assert data["log_bytes"] == 0

    def test_org_log_usage_requires_admin(self, db_server_env):
        client, _, pub_token, _ = db_server_env
        resp = client.get(
            "/v1/admin/quota/logs/test-org",
            headers=self._admin_headers(pub_token),
        )
        assert resp.status_code == 403
