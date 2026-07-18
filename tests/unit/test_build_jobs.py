"""Tests for the build job queue and DAG scheduling — DB store and REST endpoints."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for build job tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import BuildJobAlreadyClaimedError, BuildJobStatus, TokenRole

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

    def test_reap_unschedulable_marks_orphans(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            # No builder serves wasm/wasm32; one does serve linux/x86_64.
            orphan = await store.create(
                recipe_name="zlib",
                platform="wasm",
                arch="wasm32",
                submitted_by="test-admin",
                recipe_version="1.3.1",
            )
            served = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                recipe_version="1.3.1",
            )
            # min_age_seconds=0 → every pending job is past the grace period.
            reaped = await store.reap_unschedulable({("linux", "x86_64")}, set(), min_age_seconds=0)
            reaped_ids = {j.id for j in reaped}
            assert orphan.id in reaped_ids
            assert served.id not in reaped_ids

            o = await store.get(orphan.id)
            s = await store.get(served.id)
            assert o.status == BuildJobStatus.unschedulable
            assert "no registered builder" in (o.error_message or "")
            assert s.status == BuildJobStatus.pending

        self._run(_test())

    def test_reap_unschedulable_respects_grace_period(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            orphan = await store.create(
                recipe_name="zlib",
                platform="wasm",
                arch="wasm32",
                submitted_by="test-admin",
                recipe_version="1.3.1",
            )
            # Just-submitted job is still within a long grace period.
            reaped = await store.reap_unschedulable(set(), set(), min_age_seconds=3600)
            assert reaped == []
            assert (await store.get(orphan.id)).status == BuildJobStatus.pending

        self._run(_test())

    def test_reap_unschedulable_platform_wildcard(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            orphan = await store.create(
                recipe_name="zlib",
                platform="wasm",
                arch="wasm32",
                submitted_by="test-admin",
                recipe_version="1.3.1",
            )
            # A legacy platform-only cross target covers wasm for any arch.
            reaped = await store.reap_unschedulable(set(), {"wasm"}, min_age_seconds=0)
            assert reaped == []
            assert (await store.get(orphan.id)).status == BuildJobStatus.pending

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

    def test_reap_timed_out_running_with_null_started_at(self):
        """A running job whose started_at is NULL must still be reaped (via
        the submitted_at fallback).  Such rows previously stayed running
        forever and pinned a builder's current_jobs at capacity — the exact
        way both openbsd builders wedged."""
        import datetime

        from sqlalchemy import select

        from cvcpkg.server.db import BuildJobRow, get_session
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="gettext",
                platform="openbsd",
                arch="x86_64",
                submitted_by="admin",
                timeout_seconds=60,
            )
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
            async with get_session() as session:
                row = (
                    await session.execute(select(BuildJobRow).where(BuildJobRow.id == job.id))
                ).scalar()
                row.status = BuildJobStatus.running
                row.started_at = None  # corrupt/abandoned running row
                row.submitted_at = old

            reaped = await store.reap_timed_out(default_timeout=86400)
            assert len(reaped) == 1
            assert reaped[0].id == job.id
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

    def test_force_cancel_running(self):
        """force=True on a running job transitions to cancelled and reconciles builder count."""
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
            # Fake the builder having claimed 1 job
            await bstore.heartbeat(builder.id, current_jobs=1)

            result = await store.cancel(job.id, force=True)
            assert result.status == BuildJobStatus.cancelled
            assert result.finished_at is not None

            builder_after = await bstore.get(builder.id)
            assert builder_after.current_jobs == 0

        self._run(_test())

    def test_force_cancel_no_op_on_terminal(self):
        """force=True still doesn't resurrect a succeeded/failed/cancelled job."""
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
            result = await store.cancel(job.id, force=True)
            assert result.status == BuildJobStatus.succeeded

        self._run(_test())

    def test_list_active_by_builder(self):
        """list_active_by_builder returns only running/dispatched jobs for a builder."""
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
            # b1: one dispatched, one running, one succeeded
            j_disp = await store.create(
                recipe_name="a", platform="linux", arch="x86_64", submitted_by="admin"
            )
            await store.dispatch(j_disp.id, b1.id)
            j_run = await store.create(
                recipe_name="b", platform="linux", arch="x86_64", submitted_by="admin"
            )
            await store.claim(j_run.id, b1.id)
            j_done = await store.create(
                recipe_name="c", platform="linux", arch="x86_64", submitted_by="admin"
            )
            await store.claim(j_done.id, b1.id)
            await store.complete(j_done.id)
            # b2: one running
            j_other = await store.create(
                recipe_name="d", platform="linux", arch="x86_64", submitted_by="admin"
            )
            await store.claim(j_other.id, b2.id)

            active = await store.list_active_by_builder(b1.id)
            ids = sorted(j.id for j in active)
            assert ids == sorted([j_disp.id, j_run.id])

        self._run(_test())

    def test_claim_running_job_is_refused(self):
        """Claiming a job another worker is running must be refused.

        This used to assert a "no-op" that still returned the job info to the
        second builder.  The row was not stolen, but the caller walked away
        holding the job and built the same variant concurrently -- two builds
        racing one publish.  A rival must be told no.
        """
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
            with pytest.raises(BuildJobAlreadyClaimedError):
                await store.claim(job.id, b2.id)

            # b1 keeps it.
            still = await store.get(job.id)
            assert still.status == BuildJobStatus.running
            assert still.builder_id == b1.id

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

    def test_cancel_dag_prefix_match(self):
        """A trailing '*' cancels every sub-DAG of a PR run, not other PRs.

        submit-dag splits one logical DAG into
        ``pr-<n>-<run>-<platform>-<arch>-<config>-<link>`` sub-DAGs, and a
        superseded CI run leaves several such orphans; the cleanup cancels them
        by the ``pr-<n>-<run>-*`` prefix.  ``pr-288-*`` must NOT reach
        ``pr-2881-*`` -- the trailing dash makes the prefix unambiguous.
        """
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            one = lambda name: [  # noqa: E731
                {"recipe_name": name, "platform": "linux", "arch": "x86_64", "depends_on": []}
            ]
            await store.create_dag(one("a"), "pr-288-run1-linux-x86_64-release-shared", "admin")
            await store.create_dag(one("b"), "pr-288-run1-windows-x86_64-release-shared", "admin")
            await store.create_dag(one("c"), "pr-288-run2-linux-x86_64-release-shared", "admin")
            other = await store.create_dag(
                one("d"), "pr-2881-run1-linux-x86_64-release-shared", "admin"
            )

            # Prefix cancels all three pr-288 sub-DAGs; pr-2881 is untouched.
            count = await store.cancel_dag("pr-288-*")
            assert count == 3
            assert (await store.get(other[0].id)).status == BuildJobStatus.pending

            # Exact match still works and is unaffected by the '*' path.
            assert await store.cancel_dag("pr-2881-run1-linux-x86_64-release-shared") == 1

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
            info = await bstore.heartbeat(builder.id, current_jobs=0, reconcile=True)
            assert info.current_jobs == 2  # reconciled from DB, not client

            # Without reconcile, trusts the client
            info = await bstore.heartbeat(builder.id, current_jobs=0, reconcile=False)
            assert info.current_jobs == 0  # client value used

        self._run(_test())

    def test_stuck_running_job_wedges_then_reap_frees_builder(self):
        """End-to-end regression for the openbsd builder wedge.

        Two jobs stuck ``running`` with a null ``started_at`` make the
        heartbeat reconcile report the builder as full (current_jobs ==
        max_jobs), so the scheduler stops giving it work.  After
        reap_timed_out clears them, the next reconcile frees the builder.
        """
        import datetime

        from sqlalchemy import select

        from cvcpkg.server.db import BuildJobRow, get_session
        from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

        async def _test():
            bstore = DbBuilderStore()
            builder = await bstore.register(
                name="obsd",
                platform="openbsd",
                arch="x86_64",
                registered_by="a",
                max_jobs=2,
            )
            store = DbBuildJobStore()

            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
            job_ids = []
            for recipe in ("gettext", "libunistring"):
                j = await store.create(
                    recipe_name=recipe,
                    platform="openbsd",
                    arch="x86_64",
                    submitted_by="admin",
                    timeout_seconds=60,
                )
                await store.dispatch(j.id, builder.id)
                await store.claim(j.id, builder.id)
                job_ids.append(j.id)

            # Corrupt both into the abandoned-running state (null started_at,
            # old submitted_at) that the old reaper skipped.
            async with get_session() as session:
                for jid in job_ids:
                    row = (
                        await session.execute(select(BuildJobRow).where(BuildJobRow.id == jid))
                    ).scalar()
                    row.status = BuildJobStatus.running
                    row.started_at = None
                    row.submitted_at = old

            # Wedged: reconcile counts both stuck rows → builder looks full.
            info = await bstore.heartbeat(builder.id, current_jobs=0, reconcile=True)
            assert info.current_jobs == 2
            assert info.current_jobs >= builder.max_jobs  # scheduler would skip it

            # The reaper clears them...
            reaped = await store.reap_timed_out(default_timeout=86400)
            assert {r.id for r in reaped} == set(job_ids)

            # ...and the next reconcile frees the builder.
            info = await bstore.heartbeat(builder.id, current_jobs=0, reconcile=True)
            assert info.current_jobs == 0

        self._run(_test())

    def test_reap_timed_out_leaves_fresh_running_job(self):
        """Guard against over-reaping: a running job started well within its
        timeout must NOT be reaped."""
        import datetime

        from sqlalchemy import select

        from cvcpkg.server.db import BuildJobRow, get_session
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="admin",
                timeout_seconds=3600,
            )
            async with get_session() as session:
                row = (
                    await session.execute(select(BuildJobRow).where(BuildJobRow.id == job.id))
                ).scalar()
                row.status = BuildJobStatus.running
                row.started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    seconds=30
                )

            reaped = await store.reap_timed_out(default_timeout=86400)
            assert reaped == []

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

    # ── Force-cancel + cascade tests ────────────────────────────

    def test_cancel_running_no_force_noop_api(self, db_server_env):
        """POST /cancel on a running job without ?force= is a no-op returning status=running."""
        client, admin_tok, pub_tok, _ = db_server_env
        # Submit + register a builder + claim to running
        sub = self._submit(client, pub_tok)
        job_id = sub.json()["id"]
        breg = client.post(
            "/v1/builders/register",
            json={"name": "b", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert breg.status_code == 200, breg.text
        bid = breg.json()["id"]
        claim = client.post(
            f"/v1/builds/{job_id}/claim",
            json={"builder_id": bid},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert claim.status_code == 200, claim.text
        assert claim.json()["status"] == "running"

        resp = client.post(
            f"/v1/builds/{job_id}/cancel",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["message"] == "no-op"

    def test_cancel_running_with_force_cascades(self, db_server_env):
        """POST /cancel?force=true on a running job cancels it AND cancels downstream."""
        client, admin_tok, pub_tok, _ = db_server_env
        # DAG: a -> b -> c
        resp = client.post(
            "/v1/builds/dag",
            json={
                "dag_id": "force-cancel-dag",
                "jobs": [
                    {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                    {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                    {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
                ],
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        job_a = resp.json()["jobs"][0]["id"]

        breg = client.post(
            "/v1/builders/register",
            json={"name": "b1", "platform": "linux", "arch": "x86_64"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        bid = breg.json()["id"]
        client.post(
            f"/v1/builds/{job_a}/claim",
            json={"builder_id": bid},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )

        resp = client.post(
            f"/v1/builds/{job_a}/cancel",
            params={"force": "true"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["cascaded"] == 2  # b and c

    def test_fail_cascades_downstream(self, db_server_env):
        """POST /fail cancels downstream dependents (parity with WS path)."""
        client, admin_tok, pub_tok, _ = db_server_env
        resp = client.post(
            "/v1/builds/dag",
            json={
                "dag_id": "fail-cascade-dag",
                "jobs": [
                    {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                    {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                    {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
                ],
            },
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        job_a = jobs[0]["id"]

        resp = client.post(
            f"/v1/builds/{job_a}/fail",
            json={"error_message": "boom"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200, resp.text

        # Verify b and c are now cancelled
        for j in jobs[1:]:
            info = client.get(
                f"/v1/builds/{j['id']}",
                headers={"Authorization": f"Bearer {pub_tok}"},
            ).json()
            assert info["status"] == "cancelled", info
