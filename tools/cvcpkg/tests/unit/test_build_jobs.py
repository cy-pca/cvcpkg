"""Tests for the build job queue and DAG scheduling — DB store and REST endpoints."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for build job tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import BuildJobStatus, TokenRole

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
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            j2 = await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                depends_on=[j1.id],
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
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="macos",
                arch="arm64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="cmake",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                org_slug="myorg",
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
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
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
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            claimed = await store.claim(job.id, builder.id)
            assert claimed.status == BuildJobStatus.running
            assert claimed.builder_id == builder.id
            assert claimed.started_at is not None

        self._run(_test())

    def test_complete(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
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
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            failed = await store.fail(job.id, error_message="cmake error")
            assert failed.status == BuildJobStatus.failed
            assert failed.error_message == "cmake error"

        self._run(_test())

    def test_complete_clears_error_message(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            await store.fail(job.id, error_message="cmake error")
            completed = await store.complete(job.id, result_archive_url="/v1/download/zlib.tar.zst")
            assert completed.status == BuildJobStatus.succeeded
            assert completed.error_message == ""

        self._run(_test())

    def test_find_ready_jobs_no_deps(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
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
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                depends_on=[j1.id],
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
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                depends_on=[j1.id],
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

    def test_pause_pending_job(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            result = await store.pause(job.id)
            assert result.status == BuildJobStatus.paused

        self._run(_test())

    def test_resume_paused_job(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.pause(job.id)
            result = await store.resume(job.id)
            assert result.status == BuildJobStatus.pending

        self._run(_test())

    def test_pause_running_job_noop(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            result = await store.pause(job.id)
            assert result.status == BuildJobStatus.running

        self._run(_test())

    def test_resume_non_paused_job_noop(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            result = await store.resume(job.id)
            assert result.status == BuildJobStatus.pending

        self._run(_test())

    def test_pause_dag(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            ]
            infos = await store.create_dag(jobs, "dag-pause", "admin")
            count = await store.pause_dag("dag-pause")
            assert count == 2

            for info in infos:
                fetched = await store.get(info.id)
                assert fetched.status == BuildJobStatus.paused

        self._run(_test())

    def test_resume_dag(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            ]
            infos = await store.create_dag(jobs, "dag-resume", "admin")
            await store.pause_dag("dag-resume")
            count = await store.resume_dag("dag-resume")
            assert count == 2

            for info in infos:
                fetched = await store.get(info.id)
                assert fetched.status == BuildJobStatus.pending

        self._run(_test())

    def test_paused_job_not_in_find_ready(self):
        """Paused jobs should not be returned by find_ready_jobs."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.pause(job.id)
            ready = await store.find_ready_jobs()
            assert all(r.id != job.id for r in ready)

        self._run(_test())

    def test_dispatch(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            dispatched = await store.dispatch(job.id, builder.id)
            assert dispatched.status == BuildJobStatus.dispatched
            assert dispatched.builder_id == builder.id

        self._run(_test())

    def test_reap_timed_out(self):
        import datetime

        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                timeout_seconds=60,
            )
            await store.claim(job.id, builder.id)

            # Force started_at to be old
            from sqlalchemy import select

            from cvcpkg.server.db import BuildJobRow, get_session

            async with get_session() as session:
                row = (
                    await session.execute(select(BuildJobRow).where(BuildJobRow.id == job.id))
                ).scalar()
                row.started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    seconds=120
                )

            reaped = await store.reap_timed_out(default_timeout=86400)
            assert len(reaped) == 1
            assert reaped[0].status == BuildJobStatus.timed_out

        self._run(_test())

    def test_priority_ordering(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="low",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                priority=0,
            )
            await store.create(
                recipe_name="high",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                priority=10,
            )
            await store.create(
                recipe_name="mid",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                priority=5,
            )
            ready = await store.find_ready_jobs()
            assert ready[0].recipe_name == "high"
            assert ready[1].recipe_name == "mid"
            assert ready[2].recipe_name == "low"

        self._run(_test())

    # ── State transition guard tests ────────────────────────────

    def test_cancel_running_job_noop(self):
        """Cancel on a running job should be a no-op (only pending/dispatched)."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            result = await store.cancel(job.id)
            assert result.status == BuildJobStatus.running  # unchanged

        self._run(_test())

    def test_cancel_succeeded_job_noop(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.complete(job.id)
            result = await store.cancel(job.id)
            assert result.status == BuildJobStatus.succeeded

        self._run(_test())

    def test_cancel_already_cancelled_noop(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.cancel(job.id)
            result = await store.cancel(job.id)
            assert result.status == BuildJobStatus.cancelled

        self._run(_test())

    def test_claim_running_job_noop(self):
        """Claiming an already-running job should be a no-op."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            b1 = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            b2 = await bstore.register(
                name="b2",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, b1.id)
            result = await store.claim(job.id, b2.id)
            assert result.status == BuildJobStatus.running
            assert result.builder_id == b1.id  # still b1

        self._run(_test())

    def test_claim_cancelled_job_noop(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.cancel(job.id)
            result = await store.claim(job.id, builder.id)
            assert result.status == BuildJobStatus.cancelled

        self._run(_test())

    def test_claim_not_found(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            result = await store.claim(9999, 1)
            assert result is None

        self._run(_test())

    def test_complete_not_found(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            result = await store.complete(9999)
            assert result is None

        self._run(_test())

    def test_fail_not_found(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            result = await store.fail(9999, error_message="oops")
            assert result is None

        self._run(_test())

    def test_dispatch_not_found(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            result = await store.dispatch(9999, 1)
            assert result is None

        self._run(_test())

    def test_dispatch_already_dispatched_noop(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            b1 = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            b2 = await bstore.register(
                name="b2",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.dispatch(job.id, b1.id)
            result = await store.dispatch(job.id, b2.id)
            assert result.status == BuildJobStatus.dispatched
            assert result.builder_id == b1.id

        self._run(_test())

    def test_dispatch_running_noop(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            result = await store.dispatch(job.id, builder.id)
            assert result.status == BuildJobStatus.running

        self._run(_test())

    # ── find_ready_jobs edge cases ──────────────────────────────

    def test_find_ready_jobs_dep_failed_blocks(self):
        """A job whose dependency failed should NOT appear as ready."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                depends_on=[j1.id],
            )
            await store.fail(j1.id, error_message="error")
            ready = await store.find_ready_jobs()
            assert len(ready) == 0

        self._run(_test())

    def test_find_ready_jobs_excludes_dispatched(self):
        """find_ready_jobs should only return pending jobs."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.dispatch(j1.id, builder.id)
            ready = await store.find_ready_jobs()
            assert len(ready) == 0

        self._run(_test())

    def test_find_ready_jobs_mixed_deps(self):
        """Job with mix of succeeded and pending deps is NOT ready."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            j2 = await store.create(
                recipe_name="openssl",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="curl",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                depends_on=[j1.id, j2.id],
            )
            # Complete only zlib, leave openssl pending
            await store.complete(j1.id)
            ready = await store.find_ready_jobs()
            # curl should NOT be ready; openssl should be
            names = [j.recipe_name for j in ready]
            assert "curl" not in names
            assert "openssl" in names

        self._run(_test())

    # ── DAG edge cases ──────────────────────────────────────────

    def test_create_dag_single_job(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "zlib", "platform": "linux", "arch": "x86_64", "depends_on": []},
            ]
            infos = await store.create_dag(jobs, "single-dag", "admin")
            assert len(infos) == 1
            assert infos[0].dag_id == "single-dag"
            assert infos[0].depends_on == []

        self._run(_test())

    def test_create_dag_invalid_dep_indices_dropped(self):
        """Out-of-range dependency indices should be silently dropped."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {
                    "recipe_name": "zlib",
                    "platform": "linux",
                    "arch": "x86_64",
                    "depends_on": [99],
                },  # invalid index
            ]
            infos = await store.create_dag(jobs, "bad-deps", "admin")
            assert len(infos) == 1
            assert infos[0].depends_on == []  # invalid dep dropped

        self._run(_test())

    def test_create_dag_diamond_graph(self):
        """Diamond: A→{B,C}→D — D depends on both B and C."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "A", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "B", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "C", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "D", "platform": "linux", "arch": "x86_64", "depends_on": [1, 2]},
            ]
            infos = await store.create_dag(jobs, "diamond", "admin")
            assert len(infos) == 4
            assert infos[0].depends_on == []
            assert infos[1].depends_on == [infos[0].id]
            assert infos[2].depends_on == [infos[0].id]
            assert set(infos[3].depends_on) == {infos[1].id, infos[2].id}

        self._run(_test())

    def test_cancel_dag_nonexistent(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            count = await store.cancel_dag("no-such-dag")
            assert count == 0

        self._run(_test())

    def test_cancel_dag_mixed_statuses(self):
        """Only pending/dispatched jobs in DAG should be cancelled."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            ]
            infos = await store.create_dag(jobs, "mixed", "admin")
            # Claim and complete job a
            await store.claim(infos[0].id, builder.id)
            await store.complete(infos[0].id)
            # Cancel DAG — only b and c should be cancelled
            count = await store.cancel_dag("mixed")
            assert count == 2
            a = await store.get(infos[0].id)
            assert a.status == BuildJobStatus.succeeded  # unchanged

        self._run(_test())

    # ── cancel_downstream edge cases ────────────────────────────

    def test_cancel_downstream_diamond(self):
        """Diamond graph: failing A should cancel B, C, and D."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "A", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "B", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "C", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "D", "platform": "linux", "arch": "x86_64", "depends_on": [1, 2]},
            ]
            infos = await store.create_dag(jobs, "diamond-ds", "admin")
            await store.fail(infos[0].id, error_message="fail")
            count = await store.cancel_downstream(infos[0].id)
            assert count == 3
            for i in [1, 2, 3]:
                j = await store.get(infos[i].id)
                assert j.status == BuildJobStatus.cancelled

        self._run(_test())

    def test_cancel_downstream_no_dependants(self):
        """cancel_downstream on a leaf job should cancel 0."""
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            count = await store.cancel_downstream(job.id)
            assert count == 0

        self._run(_test())

    def test_cancel_downstream_skips_running(self):
        """cancel_downstream should skip already-running jobs."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            jobs = [
                {"recipe_name": "A", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "B", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            ]
            infos = await store.create_dag(jobs, "ds-skip", "admin")
            # Start B running before A fails
            await store.claim(infos[1].id, builder.id)
            await store.fail(infos[0].id, error_message="fail")
            count = await store.cancel_downstream(infos[0].id)
            assert count == 0  # B is running, not cancelled
            b = await store.get(infos[1].id)
            assert b.status == BuildJobStatus.running

        self._run(_test())

    # ── list_jobs filter tests ──────────────────────────────────

    def test_list_jobs_status_filter(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.cancel(j1.id)
            cancelled, total = await store.list_jobs(status=BuildJobStatus.cancelled)
            assert total == 1
            assert cancelled[0].recipe_name == "zlib"
            pending, total = await store.list_jobs(status=BuildJobStatus.pending)
            assert total == 1
            assert pending[0].recipe_name == "boost"

        self._run(_test())

    def test_list_jobs_dag_filter(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create_dag(
                [{"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []}],
                "dag-A",
                "admin",
            )
            await store.create_dag(
                [{"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": []}],
                "dag-B",
                "admin",
            )
            jobs, total = await store.list_jobs(dag_id="dag-A")
            assert total == 1
            assert jobs[0].recipe_name == "a"

        self._run(_test())

    def test_list_jobs_recipe_name_filter(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            jobs, total = await store.list_jobs(recipe_name="boost")
            assert total == 1
            assert jobs[0].recipe_name == "boost"

        self._run(_test())

    def test_list_jobs_builder_id_filter(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(j1.id, builder.id)
            jobs, total = await store.list_jobs(builder_id=builder.id)
            assert total == 1
            assert jobs[0].recipe_name == "zlib"

        self._run(_test())

    def test_list_jobs_pagination(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            for i in range(5):
                await store.create(
                    recipe_name=f"pkg-{i}",
                    platform="linux",
                    arch="x86_64",
                    submitted_by="admin",
                )
            page1, total = await store.list_jobs(limit=2, offset=0)
            assert total == 5
            assert len(page1) == 2
            page2, _ = await store.list_jobs(limit=2, offset=2)
            assert len(page2) == 2
            page3, _ = await store.list_jobs(limit=2, offset=4)
            assert len(page3) == 1

        self._run(_test())

    def test_list_jobs_offset_beyond_total(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            jobs, total = await store.list_jobs(limit=10, offset=100)
            assert total == 1
            assert len(jobs) == 0

        self._run(_test())

    # ── reap_timed_out edge cases ───────────────────────────────

    def test_reap_timed_out_custom_vs_default_timeout(self):
        """Job with custom timeout_seconds should use that, not default."""
        import datetime

        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            # Job with 30-second custom timeout
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                timeout_seconds=60,
            )
            await store.claim(job.id, builder.id)

            # Force started_at to 90 seconds ago (exceeds 60s custom timeout)
            from sqlalchemy import select

            from cvcpkg.server.db import BuildJobRow, get_session

            async with get_session() as session:
                row = (
                    await session.execute(select(BuildJobRow).where(BuildJobRow.id == job.id))
                ).scalar()
                row.started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    seconds=90
                )

            # Default timeout is very large, but custom should trigger
            reaped = await store.reap_timed_out(default_timeout=86400)
            assert len(reaped) == 1

        self._run(_test())

    def test_reap_timed_out_none_timed_out(self):
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.claim(job.id, builder.id)
            # Just started, shouldn't be timed out
            reaped = await store.reap_timed_out(default_timeout=86400)
            assert len(reaped) == 0

        self._run(_test())

    def test_complete_reconciles_builder_job_count(self):
        """complete() should update the builder's current_jobs from actual DB state."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()

            # Create 3 jobs and dispatch them all to the builder
            jobs = []
            for name in ("zlib", "boost", "gmp"):
                j = await store.create(
                    recipe_name=name,
                    platform="linux",
                    arch="x86_64",
                    submitted_by="admin",
                )
                await store.dispatch(j.id, builder.id)
                await store.claim(j.id, builder.id)
                jobs.append(j)

            # Simulate server thinking builder has 3 jobs
            await bstore.heartbeat(builder.id, current_jobs=3)
            b = await bstore.get(builder.id)
            assert b.current_jobs == 3

            # Complete one job — builder count should reconcile to 2
            await store.complete(jobs[0].id)
            b = await bstore.get(builder.id)
            assert b.current_jobs == 2

            # Complete second job — count should reconcile to 1
            await store.complete(jobs[1].id)
            b = await bstore.get(builder.id)
            assert b.current_jobs == 1

            # Complete last job — count should be 0
            await store.complete(jobs[2].id)
            b = await bstore.get(builder.id)
            assert b.current_jobs == 0

        self._run(_test())

    def test_fail_reconciles_builder_job_count(self):
        """fail() should update the builder's current_jobs from actual DB state."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()

            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            j2 = await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.dispatch(j1.id, builder.id)
            await store.claim(j1.id, builder.id)
            await store.dispatch(j2.id, builder.id)
            await store.claim(j2.id, builder.id)

            # Simulate stale count (e.g., heartbeat set it to 5 by mistake)
            await bstore.heartbeat(builder.id, current_jobs=5)
            b = await bstore.get(builder.id)
            assert b.current_jobs == 5

            # Fail one job — should reconcile to 1
            await store.fail(j1.id, error_message="build error")
            b = await bstore.get(builder.id)
            assert b.current_jobs == 1

        self._run(_test())

    def test_heartbeat_reconcile_overrides_client_value(self):
        """heartbeat(reconcile=True) uses DB count, not client-reported value."""
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="b1",
                platform="linux",
                arch="x86_64",
                registered_by="a",
            )
            store = DbBuildJobStore()

            # Create and dispatch 2 jobs
            j1 = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            j2 = await store.create(
                recipe_name="boost",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
            )
            await store.dispatch(j1.id, builder.id)
            await store.claim(j1.id, builder.id)
            await store.dispatch(j2.id, builder.id)

            # Builder reports 0 (e.g., after restart) but DB has 2 active
            info = await bstore.heartbeat(
                builder.id, current_jobs=0, reconcile=True
            )
            assert info.current_jobs == 2  # reconciled from DB, not client

            # Without reconcile, trusts the client
            info = await bstore.heartbeat(
                builder.id, current_jobs=0, reconcile=False
            )
            assert info.current_jobs == 0  # client value used

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

    # ── Validation tests ────────────────────────────────────────

    def test_submit_empty_recipe_name_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds",
            json={"recipe_name": "", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422

    def test_submit_negative_priority_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds",
            json={
                "recipe_name": "zlib",
                "platform": "linux",
                "arch": "x86_64",
                "priority": -1,
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422

    def test_submit_timeout_too_low_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds",
            json={
                "recipe_name": "zlib",
                "platform": "linux",
                "arch": "x86_64",
                "timeout_seconds": 59,
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422

    def test_submit_timeout_too_high_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds",
            json={
                "recipe_name": "zlib",
                "platform": "linux",
                "arch": "x86_64",
                "timeout_seconds": 172801,
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422

    def test_submit_dag_empty_jobs_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/dag",
            json={"jobs": []},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422

    def test_fail_error_message_too_long_rejected(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        # Need a running job first
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
            json={"error_message": "x" * 4097},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 422

    # ── 404 tests for mutation endpoints ────────────────────────

    def test_claim_not_found_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/9999/claim",
            json={"builder_id": 1},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_complete_not_found_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/9999/complete",
            json={},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_fail_not_found_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/9999/fail",
            json={},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    # ── Filter tests via API ────────────────────────────────────

    def test_list_builds_status_filter(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        sub1 = self._submit(client, pub_tok, "zlib")
        self._submit(client, pub_tok, "boost")
        job_id = sub1.json()["id"]
        client.post(
            f"/v1/builds/{job_id}/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.get(
            "/v1/builds",
            params={"status": "cancelled"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_builds_recipe_name_filter(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        self._submit(client, pub_tok, "zlib")
        self._submit(client, pub_tok, "boost")
        resp = client.get(
            "/v1/builds",
            params={"recipe_name": "zlib"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    # ── Cancel idempotency via API ──────────────────────────────

    def test_cancel_already_cancelled_api(self, db_server_env):
        """Cancelling an already-cancelled job should return 200 with status unchanged."""
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]
        client.post(
            f"/v1/builds/{job_id}/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.post(
            f"/v1/builds/{job_id}/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_dag_nonexistent_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/dag/no-such-dag/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["cancelled"] == 0

    # ── Pause / resume API tests ─────────────────────────────

    def test_pause_pending_job_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]
        resp = client.post(
            f"/v1/builds/{job_id}/pause",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_resume_paused_job_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]
        client.post(
            f"/v1/builds/{job_id}/pause",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        resp = client.post(
            f"/v1/builds/{job_id}/resume",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_pause_running_job_noop_api(self, db_server_env):
        """Pausing a running job should be a no-op."""
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]
        # Register builder and claim
        br = client.post(
            "/v1/builders/register",
            headers={"Authorization": f"Bearer {pub_tok}"},
            json={
                "name": "pause-test-builder",
                "platform": "linux",
                "arch": "x86_64",
                "max_jobs": 1,
            },
        )
        client.post(
            f"/v1/builds/{job_id}/claim",
            headers={"Authorization": f"Bearer {pub_tok}"},
            json={"builder_id": br.json()["id"]},
        )
        resp = client.post(
            f"/v1/builds/{job_id}/pause",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_resume_non_paused_job_noop_api(self, db_server_env):
        """Resuming a non-paused job should be a no-op."""
        client, admin_tok, pub_tok, _ = db_server_env
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]
        resp = client.post(
            f"/v1/builds/{job_id}/resume",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_pause_dag_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        jobs = [
            {
                "recipe_name": "a",
                "platform": "linux",
                "arch": "x86_64",
                "depends_on": [],
            },
            {
                "recipe_name": "b",
                "platform": "linux",
                "arch": "x86_64",
                "depends_on": [0],
            },
        ]
        resp = client.post(
            "/v1/builds/dag",
            headers={"Authorization": f"Bearer {pub_tok}"},
            json={"jobs": jobs, "dag_id": "pause-dag-1"},
        )
        assert resp.status_code == 200
        resp = client.post(
            "/v1/builds/dag/pause-dag-1/pause",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["paused"] == 2

    def test_resume_dag_api(self, db_server_env):
        client, admin_tok, pub_tok, _ = db_server_env
        jobs = [
            {
                "recipe_name": "a",
                "platform": "linux",
                "arch": "x86_64",
                "depends_on": [],
            },
            {
                "recipe_name": "b",
                "platform": "linux",
                "arch": "x86_64",
                "depends_on": [0],
            },
        ]
        resp = client.post(
            "/v1/builds/dag",
            headers={"Authorization": f"Bearer {pub_tok}"},
            json={"jobs": jobs, "dag_id": "resume-dag-1"},
        )
        assert resp.status_code == 200
        # Pause first
        client.post(
            "/v1/builds/dag/resume-dag-1/pause",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        # Resume
        resp = client.post(
            "/v1/builds/dag/resume-dag-1/resume",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["resumed"] == 2
