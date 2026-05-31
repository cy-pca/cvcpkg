"""Tests for the build job queue and DAG scheduling — DB store and REST endpoints."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for build job tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import AuditAction, BuildJobStatus, TokenRole

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


# ── DbBuildJobStore unit tests ──────────────────────────────────


class TestDbBuildJobStore:
    """Direct tests for the DbBuildJobStore class."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "build_job_store.db"
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

    def test_create_and_get(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            info = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                recipe_version="1.3.1",
                config="release",
                link="shared",
            )
            assert info.recipe_name == "zlib"
            assert info.platform == "linux"
            assert info.status == BuildJobStatus.pending
            assert info.depends_on == []

            fetched = await store.get(info.id)
            assert fetched is not None
            assert fetched.recipe_name == "zlib"

        self._run(_test())

    def test_get_not_found(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            result = await store.get(9999)
            assert result is None

        self._run(_test())

    def test_create_with_dependencies(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            j2 = await store.create(
                recipe_name="boost", platform="linux", arch="x86_64",
                submitted_by="admin", depends_on=[j1.id],
            )
            assert j2.depends_on == [j1.id]

            fetched = await store.get(j2.id)
            assert j1.id in fetched.depends_on

        self._run(_test())

    def test_list_with_filters(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost", platform="macos", arch="arm64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="cmake", platform="linux", arch="x86_64",
                submitted_by="admin", org_slug="myorg",
            )

            all_jobs, total = await store.list_jobs()
            assert total == 3

            linux_jobs, _ = await store.list_jobs(platform="linux")
            assert len(linux_jobs) == 2

            org_jobs, _ = await store.list_jobs(org_slug="myorg")
            assert len(org_jobs) == 1

        self._run(_test())

    def test_cancel(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            info = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            cancelled = await store.cancel(info.id)
            assert cancelled.status == BuildJobStatus.cancelled
            assert cancelled.finished_at is not None

        self._run(_test())

    def test_cancel_not_found(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            result = await store.cancel(9999)
            assert result is None

        self._run(_test())

    def test_claim(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            claimed = await store.claim(job.id, builder.id)
            assert claimed.status == BuildJobStatus.running
            assert claimed.builder_id == builder.id
            assert claimed.started_at is not None

        self._run(_test())

    def test_complete(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            completed = await store.complete(
                job.id, result_archive_url="/v1/download/zlib-1.3.1.tar.zst"
            )
            assert completed.status == BuildJobStatus.succeeded
            assert completed.result_archive_url == "/v1/download/zlib-1.3.1.tar.zst"
            assert completed.finished_at is not None

        self._run(_test())

    def test_fail(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            failed = await store.fail(job.id, error_message="cmake error")
            assert failed.status == BuildJobStatus.failed
            assert failed.error_message == "cmake error"

        self._run(_test())

    def test_find_ready_jobs_no_deps(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            ready = await store.find_ready_jobs()
            assert len(ready) == 1
            assert ready[0].recipe_name == "zlib"

        self._run(_test())

    def test_find_ready_jobs_with_unmet_deps(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost", platform="linux", arch="x86_64",
                submitted_by="admin", depends_on=[j1.id],
            )

            ready = await store.find_ready_jobs()
            # Only zlib should be ready (boost depends on zlib)
            assert len(ready) == 1
            assert ready[0].recipe_name == "zlib"

        self._run(_test())

    def test_find_ready_jobs_deps_met(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            j2 = await store.create(
                recipe_name="boost", platform="linux", arch="x86_64",
                submitted_by="admin", depends_on=[j1.id],
            )

            # Complete zlib
            await store.complete(j1.id)

            ready = await store.find_ready_jobs()
            # Now boost should be ready too
            assert len(ready) == 1
            assert ready[0].recipe_name == "boost"

        self._run(_test())

    def test_create_dag(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {
                    "recipe_name": "zlib",
                    "platform": "linux",
                    "arch": "x86_64",
                    "depends_on": [],
                },
                {
                    "recipe_name": "boost",
                    "platform": "linux",
                    "arch": "x86_64",
                    "depends_on": [0],  # depends on zlib (index 0)
                },
                {
                    "recipe_name": "vtk",
                    "platform": "linux",
                    "arch": "x86_64",
                    "depends_on": [0, 1],  # depends on zlib and boost
                },
            ]
            infos = await store.create_dag(jobs, "dag-001", "admin")
            assert len(infos) == 3
            assert all(j.dag_id == "dag-001" for j in infos)
            assert infos[0].depends_on == []
            assert infos[1].depends_on == [infos[0].id]
            assert set(infos[2].depends_on) == {infos[0].id, infos[1].id}

        self._run(_test())

    def test_cancel_dag(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            ]
            infos = await store.create_dag(jobs, "dag-cancel", "admin")
            count = await store.cancel_dag("dag-cancel")
            assert count == 2

            for info in infos:
                fetched = await store.get(info.id)
                assert fetched.status == BuildJobStatus.cancelled

        self._run(_test())

    def test_cancel_downstream(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
            ]
            infos = await store.create_dag(jobs, "dag-ds", "admin")
            # Fail job a
            await store.fail(infos[0].id, error_message="build error")
            # Cancel downstream
            count = await store.cancel_downstream(infos[0].id)
            assert count == 2  # b and c cancelled

            for info in infos[1:]:
                fetched = await store.get(info.id)
                assert fetched.status == BuildJobStatus.cancelled

        self._run(_test())

    def test_dispatch(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin",
            )
            dispatched = await store.dispatch(job.id, builder.id)
            assert dispatched.status == BuildJobStatus.dispatched
            assert dispatched.builder_id == builder.id

        self._run(_test())

    def test_reap_timed_out(self):
        import datetime

        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1", platform="linux", arch="x86_64", registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib", platform="linux", arch="x86_64",
                submitted_by="admin", timeout_seconds=60,
            )
            await store.claim(job.id, builder.id)

            # Force started_at to be old
            from cvcpkg.server.db import BuildJobRow, get_session
            from sqlalchemy import select

            async with get_session() as session:
                row = (await session.execute(
                    select(BuildJobRow).where(BuildJobRow.id == job.id)
                )).scalar()
                row.started_at = datetime.datetime.now(
                    datetime.timezone.utc
                ) - datetime.timedelta(seconds=120)

            reaped = await store.reap_timed_out(default_timeout=86400)
            assert len(reaped) == 1
            assert reaped[0].status == BuildJobStatus.timed_out

        self._run(_test())

    def test_priority_ordering(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="low", platform="linux", arch="x86_64",
                submitted_by="admin", priority=0,
            )
            await store.create(
                recipe_name="high", platform="linux", arch="x86_64",
                submitted_by="admin", priority=10,
            )
            await store.create(
                recipe_name="mid", platform="linux", arch="x86_64",
                submitted_by="admin", priority=5,
            )
            ready = await store.find_ready_jobs()
            assert ready[0].recipe_name == "high"
            assert ready[1].recipe_name == "mid"
            assert ready[2].recipe_name == "low"

        self._run(_test())


# ── API endpoint tests ──────────────────────────────────────────


class TestBuildJobEndpoints:
    """Test build job REST endpoints via TestClient."""

    def _submit(self, client, token, recipe_name="zlib", platform="linux", arch="x86_64"):
        return client.post(
            "/v1/builds",
            json={
                "recipe_name": recipe_name,
                "platform": platform,
                "arch": arch,
                "config": "release",
                "link": "shared",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_submit_build(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = self._submit(client, pub_tok)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["recipe_name"] == "zlib"
        assert data["status"] == "pending"

    def test_submit_requires_auth(self, db_server_env):
        client, *_ = db_server_env
        resp = client.post(
            "/v1/builds",
            json={"recipe_name": "zlib", "platform": "linux", "arch": "x86_64"},
        )
        assert resp.status_code == 401

    def test_list_builds(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        self._submit(client, pub_tok, "zlib")
        self._submit(client, pub_tok, "boost")

        resp = client.get(
            "/v1/builds",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_list_builds_with_filter(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        self._submit(client, pub_tok, "zlib", "linux", "x86_64")
        self._submit(client, pub_tok, "boost", "macos", "arm64")

        resp = client.get(
            "/v1/builds",
            params={"platform": "linux"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_get_build(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]

        resp = client.get(
            f"/v1/builds/{job_id}",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_get_build_not_found(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.get(
            "/v1/builds/9999",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_cancel_build(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]

        resp = client.post(
            f"/v1/builds/{job_id}/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_not_found(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/9999/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_submit_dag(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/dag",
            json={
                "jobs": [
                    {
                        "recipe_name": "zlib",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [],
                    },
                    {
                        "recipe_name": "boost",
                        "platform": "linux",
                        "arch": "x86_64",
                        "depends_on": [0],
                    },
                ],
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        assert data["dag_id"]
        jobs = data["jobs"]
        assert jobs[0]["depends_on"] == []
        assert jobs[1]["depends_on"] == [jobs[0]["id"]]

    def test_cancel_dag(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        dag_resp = client.post(
            "/v1/builds/dag",
            json={
                "dag_id": "test-dag",
                "jobs": [
                    {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                    {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                ],
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert dag_resp.status_code == 200

        resp = client.post(
            "/v1/builds/dag/test-dag/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["cancelled"] == 2

    def test_claim_build(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        # Register a builder
        builder_resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        builder_id = builder_resp.json()["id"]

        # Submit a job
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]

        # Claim it
        resp = client.post(
            f"/v1/builds/{job_id}/claim",
            json={"builder_id": builder_id},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["builder_id"] == builder_id

    def test_complete_build(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        builder_resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        builder_id = builder_resp.json()["id"]

        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]

        client.post(
            f"/v1/builds/{job_id}/claim",
            json={"builder_id": builder_id},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )

        resp = client.post(
            f"/v1/builds/{job_id}/complete",
            json={"result_archive_url": "/v1/download/zlib.tar.zst"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "succeeded"

    def test_fail_build(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        builder_resp = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        builder_id = builder_resp.json()["id"]

        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]

        client.post(
            f"/v1/builds/{job_id}/claim",
            json={"builder_id": builder_id},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )

        resp = client.post(
            f"/v1/builds/{job_id}/fail",
            json={"error_message": "cmake failed"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_message"] == "cmake failed"

    def test_empty_build_list(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.get(
            "/v1/builds",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["jobs"] == []
