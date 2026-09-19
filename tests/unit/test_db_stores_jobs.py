"""Direct unit tests for the DB builder, build-job, recipe, and webhook stores.

Same harness shape as ``test_db_stores_paths.py``: a fresh file-backed
aiosqlite database per test, all async work inside one coroutine handed to
:func:`run`.  Covers registration/upsert, the job lifecycle (create → dispatch
→ claim → complete/fail/cancel), DAG scheduling and readiness, the reaper
sweeps, log accounting, and each store's not-found / wrong-state branch.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required for db_stores tests")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required for db_stores tests")


@pytest.fixture(autouse=True)
def _db_store_env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _init():
        init_db(db_url)
        await create_tables()

    asyncio.run(_init())
    try:
        yield
    finally:
        asyncio.run(dispose_engine())


def run(coro):
    return asyncio.run(coro)


async def _set_job(job_id, **values):
    from sqlalchemy import update

    from cvcpkg.server.db import BuildJobRow, get_session

    async with get_session() as session:
        await session.execute(
            update(BuildJobRow).where(BuildJobRow.id == job_id).values(**values)
        )


# ── DbBuilderStore ──────────────────────────────────────────────


class TestDbBuilderStore:
    def test_register_new_and_reregister(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            store = DbBuilderStore()
            info = await store.register("bx", "linux", "x86_64", "root",
                                        labels=["gpu"], max_jobs=4)
            assert info.name == "bx"
            assert info.status == "online"
            assert info.max_jobs == 4
            assert info.labels == ["gpu"]
            # Home org is always in the served set.
            assert "" in info.served_namespaces
            # Re-register (same name+org) upserts in place, not a new row.
            info2 = await store.register("bx", "windows", "arm64", "root2", max_jobs=8)
            assert info2.id == info.id
            assert info2.platform == "windows" and info2.arch == "arm64"
            assert info2.max_jobs == 8
            assert len(await store.list_builders()) == 1

        run(_t())

    def test_get_and_get_by_name(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            store = DbBuilderStore()
            info = await store.register("bx", "linux", "x86_64", "root")
            assert (await store.get(info.id)).name == "bx"
            assert await store.get(9999) is None
            assert (await store.get_by_name("bx")).id == info.id
            assert await store.get_by_name("ghost") is None

        run(_t())

    def test_list_builders_filters(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            store = DbBuilderStore()
            await store.register("lin", "linux", "x86_64", "root")
            await store.register("win", "windows", "x86_64", "root")
            assert len(await store.list_builders(platform="linux")) == 1
            assert len(await store.list_builders(arch="x86_64")) == 2
            assert len(await store.list_builders(status="online")) == 2
            assert len(await store.list_builders(status="offline")) == 0
            assert len(await store.list_builders(org_slug="")) == 2

        run(_t())

    def test_update_found_and_missing(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            store = DbBuilderStore()
            info = await store.register("bx", "linux", "x86_64", "root")
            upd = await store.update(info.id, labels=["a"], capabilities={"cuda": "12"},
                                     max_jobs=3, prefer_affinity=True,
                                     served_namespaces=["acme"], free_disk_gb=50)
            assert upd.labels == ["a"]
            assert upd.capabilities == {"cuda": "12"}
            assert upd.max_jobs == 3
            assert upd.prefer_affinity is True
            assert upd.free_disk_gb == 50
            # served always re-anchored to include the home org ("").
            assert "" in upd.served_namespaces and "acme" in upd.served_namespaces
            assert await store.update(9999, max_jobs=1) is None

        run(_t())

    def test_heartbeat_and_reconcile(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _t():
            builders = DbBuilderStore()
            jobs = DbBuildJobStore()
            b = await builders.register("bx", "linux", "x86_64", "root", max_jobs=5)
            # Plain heartbeat trusts the client-reported count.
            hb = await builders.heartbeat(b.id, current_jobs=2, free_disk_gb=99)
            assert hb.current_jobs == 2
            assert hb.free_disk_gb == 99
            # Wire up two dispatched jobs, then reconcile from actual DB state.
            j1 = await jobs.create("z1", "linux", "x86_64", "ci")
            j2 = await jobs.create("z2", "linux", "x86_64", "ci")
            await jobs.dispatch(j1.id, b.id)
            await jobs.dispatch(j2.id, b.id)
            hb2 = await builders.heartbeat(b.id, current_jobs=999, reconcile=True)
            assert hb2.current_jobs == 2  # ignores the bogus client value
            assert await builders.heartbeat(9999) is None

        run(_t())

    def test_unregister(self):
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            store = DbBuilderStore()
            b = await store.register("bx", "linux", "x86_64", "root")
            assert await store.unregister(b.id) is True
            assert await store.unregister(b.id) is False
            assert await store.get(b.id) is None

        run(_t())

    def test_reap_stale(self):
        import datetime

        from sqlalchemy import update

        from cvcpkg.server.db import BuilderRow, get_session
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            store = DbBuilderStore()
            fresh = await store.register("fresh", "linux", "x86_64", "root")
            stale = await store.register("stale", "linux", "x86_64", "root")
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
            async with get_session() as s:
                await s.execute(
                    update(BuilderRow).where(BuilderRow.id == stale.id).values(
                        last_heartbeat=old)
                )
            reaped = await store.reap_stale(max_age_seconds=180)
            assert {r.name for r in reaped} == {"stale"}
            assert (await store.get(stale.id)).status == "offline"
            assert (await store.get(fresh.id)).status == "online"

        run(_t())

    def test_decode_bad_json_falls_back(self):
        from cvcpkg.server.db import BuilderRow, get_session
        from cvcpkg.server.db_stores import DbBuilderStore

        async def _t():
            async with get_session() as s:
                s.add(BuilderRow(
                    name="legacy", org_slug="", served_namespaces="not-json",
                    platform="linux", arch="x86_64", labels="not-json",
                    capabilities="not-json", status="online", current_jobs=0,
                    max_jobs=1, registered_by="root",
                ))
            store = DbBuilderStore()
            (info,) = await store.list_builders()
            # Bad JSON degrades gracefully: labels [], caps {}, served → home-only.
            assert info.labels == []
            assert info.capabilities == {}
            assert info.served_namespaces == [""]

        run(_t())


# ── DbBuildJobStore ─────────────────────────────────────────────


class TestDbBuildJobStore:
    def test_create_and_get(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            job = await store.create("zlib", "linux", "x86_64", "ci",
                                     required_capabilities=["cuda"], priority=5,
                                     timeout_seconds=60)
            assert job.recipe_name == "zlib"
            assert job.status == "pending"
            assert job.required_capabilities == ["cuda"]
            assert job.priority == 5
            got = await store.get(job.id)
            assert got.id == job.id
            assert await store.get(9999) is None

        run(_t())

    def test_create_dag_with_deps_dedup(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            jobs = await store.create_dag(
                [
                    {"recipe_name": "lzma", "platform": "linux", "arch": "x86_64"},
                    {"recipe_name": "zlib", "platform": "linux", "arch": "x86_64",
                     "depends_on": [0, 0]},  # duplicate index → deduped
                    {"recipe_name": "png", "platform": "linux", "arch": "x86_64",
                     "depends_on": [1, 99]},  # 99 is out of range → ignored
                ],
                dag_id="dag1",
                submitted_by="ci",
            )
            by = {j.recipe_name: j for j in jobs}
            assert by["zlib"].depends_on == [by["lzma"].id]  # deduped to one edge
            assert by["png"].depends_on == [by["zlib"].id]
            assert by["lzma"].depends_on == []
            # All three carry the dag id.
            listed, total = await store.list_jobs(dag_id="dag1")
            assert total == 3

        run(_t())

    def test_list_jobs_filters_and_visibility(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbOrgStore

        async def _t():
            orgs = DbOrgStore()
            await orgs.create(slug="pub", display_name="p", is_private=False, created_by="r")
            await orgs.create(slug="sec", display_name="s", is_private=True, created_by="r")
            await orgs.add_member("sec", "bob")
            store = DbBuildJobStore()
            await store.create("a", "linux", "x86_64", "ci")  # public base
            await store.create("b", "linux", "x86_64", "ci", org_slug="pub")
            await store.create("c", "windows", "x86_64", "ci", org_slug="sec")

            assert (await store.list_jobs(platform="windows"))[1] == 1
            assert (await store.list_jobs(recipe_name="a"))[1] == 1
            assert (await store.list_jobs(status="pending"))[1] == 3
            # Visibility: anonymous caller cannot see the private-org job.
            _, anon = await store.list_jobs(visible_to="")
            assert anon == 2
            _, member = await store.list_jobs(visible_to="bob")
            assert member == 3
            # Pagination.
            page, total = await store.list_jobs(limit=1, offset=0)
            assert total == 3 and len(page) == 1

        run(_t())

    def test_list_jobs_dag_prefix(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            await store.create_dag([{"recipe_name": "a", "platform": "linux",
                                     "arch": "x86_64"}], dag_id="pr-1-linux", submitted_by="ci")
            await store.create_dag([{"recipe_name": "b", "platform": "linux",
                                     "arch": "x86_64"}], dag_id="pr-1-windows", submitted_by="ci")
            _, total = await store.list_jobs(dag_id="pr-1-*")
            assert total == 2
            _, exact = await store.list_jobs(dag_id="pr-1-linux")
            assert exact == 1

        run(_t())

    def test_cancel_paths(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            assert await store.cancel(9999) is None
            # Pending → cancelled.
            j = await store.create("a", "linux", "x86_64", "ci")
            c = await store.cancel(j.id)
            assert c.status == "cancelled"
            # Running without force is left untouched.
            j2 = await store.create("b", "linux", "x86_64", "ci")
            await store.claim(j2.id, None, claimant="w")
            same = await store.cancel(j2.id)
            assert same.status == "running"
            # force cancels a running job.
            forced = await store.cancel(j2.id, force=True)
            assert forced.status == "cancelled"

        run(_t())

    def test_cancel_force_reconciles_builder(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _t():
            builders = DbBuilderStore()
            jobs = DbBuildJobStore()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id, claimant="w")
            await jobs.cancel(j.id, force=True)
            # No active jobs remain for the builder after the forced cancel.
            assert (await builders.get(b.id)).current_jobs == 0

        run(_t())

    def test_list_active_by_builder(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _t():
            builders = DbBuilderStore()
            jobs = DbBuildJobStore()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j1 = await jobs.create("a", "linux", "x86_64", "ci")
            j2 = await jobs.create("b", "linux", "x86_64", "ci")
            await jobs.dispatch(j1.id, b.id)
            await jobs.claim(j2.id, b.id, claimant="w")
            active = await jobs.list_active_by_builder(b.id)
            assert {j.recipe_name for j in active} == {"a", "b"}

        run(_t())

    def test_cancel_dag_exact_and_prefix(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            await store.create_dag(
                [{"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
                 {"recipe_name": "b", "platform": "linux", "arch": "x86_64"}],
                dag_id="d1", submitted_by="ci")
            n = await store.cancel_dag("d1")
            assert n == 2
            # prefix form
            await store.create_dag([{"recipe_name": "c", "platform": "linux",
                                     "arch": "x86_64"}], dag_id="pr-9-lin", submitted_by="ci")
            await store.create_dag([{"recipe_name": "d", "platform": "linux",
                                     "arch": "x86_64"}], dag_id="pr-9-win", submitted_by="ci")
            assert await store.cancel_dag("pr-9-*") == 2

        run(_t())

    def test_pause_resume(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            assert await store.pause(9999) is None
            assert await store.resume(9999) is None
            j = await store.create("a", "linux", "x86_64", "ci")
            paused = await store.pause(j.id)
            assert paused.status == "paused"
            # resume from non-paused is a no-op returning the row; here it IS paused.
            resumed = await store.resume(j.id)
            assert resumed.status == "pending"
            # resume of a pending job is a no-op (wrong state) → unchanged.
            again = await store.resume(j.id)
            assert again.status == "pending"

        run(_t())

    def test_pause_resume_dag(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            await store.create_dag(
                [{"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
                 {"recipe_name": "b", "platform": "linux", "arch": "x86_64"}],
                dag_id="d1", submitted_by="ci")
            assert await store.pause_dag("d1") == 2
            assert await store.resume_dag("d1") == 2

        run(_t())

    def test_is_dag_complete_and_summary(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            assert await store.is_dag_complete("nope") is None  # no jobs
            jobs = await store.create_dag(
                [{"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
                 {"recipe_name": "b", "platform": "linux", "arch": "x86_64"}],
                dag_id="d1", submitted_by="ci")
            assert await store.is_dag_complete("d1") is False
            await store.complete(jobs[0].id)
            await store.fail(jobs[1].id, error_message="boom")
            assert await store.is_dag_complete("d1") is True
            summary = await store.dag_summary("d1")
            assert summary["total"] == 2
            assert summary["succeeded"] == 1
            assert summary["failed"] == 1

        run(_t())

    def test_claim_success_notfound_already_terminal(self):
        from cvcpkg.server.db_stores import DbBuildJobStore
        from cvcpkg.server.models import BuildJobAlreadyClaimedError

        async def _t():
            store = DbBuildJobStore()
            assert await store.claim(9999, None) is None
            # success
            j = await store.create("a", "linux", "x86_64", "ci")
            claimed = await store.claim(j.id, None, claimant="worker-1")
            assert claimed.status == "running"
            assert claimed.claimed_by == "worker-1"
            # a second claim while running raises
            with pytest.raises(BuildJobAlreadyClaimedError):
                await store.claim(j.id, None, claimant="worker-2")
            # a terminal (cancelled) job is handed back, not an error
            j2 = await store.create("b", "linux", "x86_64", "ci")
            await store.cancel(j2.id)
            back = await store.claim(j2.id, None)
            assert back.status == "cancelled"

        run(_t())

    def test_complete_and_fail(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _t():
            builders = DbBuilderStore()
            store = DbBuildJobStore()
            b = await builders.register("bx", "linux", "x86_64", "root")
            assert await store.complete(9999) is None
            assert await store.fail(9999) is None
            j = await store.create("a", "linux", "x86_64", "ci")
            await store.claim(j.id, b.id, claimant="w")
            done = await store.complete(j.id, result_archive_url="/v1/download/a.tar.zst")
            assert done.status == "succeeded"
            assert done.result_archive_url == "/v1/download/a.tar.zst"
            # builder count reconciled to 0 after completion
            assert (await builders.get(b.id)).current_jobs == 0
            j2 = await store.create("b", "linux", "x86_64", "ci")
            await store.claim(j2.id, b.id, claimant="w")
            failed = await store.fail(j2.id, error_message="broke")
            assert failed.status == "failed"
            assert failed.error_message == "broke"

        run(_t())

    def test_find_ready_and_dispatch(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _t():
            builders = DbBuilderStore()
            b = await builders.register("bx", "linux", "x86_64", "root")
            store = DbBuildJobStore()
            jobs = await store.create_dag(
                [{"recipe_name": "dep", "platform": "linux", "arch": "x86_64"},
                 {"recipe_name": "app", "platform": "linux", "arch": "x86_64",
                  "depends_on": [0]}],
                dag_id="d1", submitted_by="ci")
            dep, app = jobs[0], jobs[1]
            ready = await store.find_ready_jobs()
            assert {j.recipe_name for j in ready} == {"dep"}  # app blocked
            await store.complete(dep.id)
            ready2 = await store.find_ready_jobs()
            assert {j.recipe_name for j in ready2} == {"app"}
            # dispatch transitions pending → dispatched; missing → None.
            assert await store.dispatch(9999, b.id) is None
            disp = await store.dispatch(app.id, b.id)
            assert disp.status == "dispatched"
            # dispatch of a non-pending job returns it unchanged.
            same = await store.dispatch(app.id, b.id)
            assert same.status == "dispatched"

        run(_t())

    def test_reap_timed_out(self):
        import datetime

        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            j = await store.create("a", "linux", "x86_64", "ci", timeout_seconds=10)
            await store.claim(j.id, None, claimant="w")  # running, started_at=now
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
            await _set_job(j.id, started_at=old)
            reaped = await store.reap_timed_out()
            assert {r.recipe_name for r in reaped} == {"a"}
            assert (await store.get(j.id)).status == "timed_out"

        run(_t())

    def test_reap_unschedulable_targets(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            covered = await store.create("ok", "linux", "x86_64", "ci")
            orphan = await store.create("bad", "freebsd", "x86_64", "ci")
            reaped = await store.reap_unschedulable(
                schedulable_targets={("linux", "x86_64")},
                schedulable_platforms=set(),
                min_age_seconds=0,
            )
            assert {r.recipe_name for r in reaped} == {"bad"}
            assert (await store.get(covered.id)).status == "pending"
            assert (await store.get(orphan.id)).status == "unschedulable"

        run(_t())

    def test_reap_unschedulable_platform_only(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            j = await store.create("x", "windows", "arm64", "ci")
            # A legacy platform-only cross target (any arch) covers it.
            reaped = await store.reap_unschedulable(
                schedulable_targets=set(),
                schedulable_platforms={"windows"},
                min_age_seconds=0,
            )
            assert reaped == []
            assert (await store.get(j.id)).status == "pending"

        run(_t())

    def test_reap_unschedulable_grace_period(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            j = await store.create("young", "freebsd", "x86_64", "ci")
            # Large min age → still within grace → not reaped.
            reaped = await store.reap_unschedulable(
                schedulable_targets=set(), schedulable_platforms=set(),
                min_age_seconds=3600)
            assert reaped == []
            assert (await store.get(j.id)).status == "pending"

        run(_t())

    def test_reap_unschedulable_capability(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            need = await store.create("cudajob", "linux", "x86_64", "ci",
                                      required_capabilities=["cuda"])
            # A builder that covers the target but lacks 'cuda' → reaped.
            reaped = await store.reap_unschedulable(
                schedulable_targets={("linux", "x86_64")},
                schedulable_platforms=set(),
                min_age_seconds=0,
                builder_offers=[({("linux", "x86_64")}, set(), set())],
            )
            assert {r.recipe_name for r in reaped} == {"cudajob"}
            assert "cuda" in (await store.get(need.id)).error_message

        run(_t())

    def test_reap_unschedulable_capability_covered(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            j = await store.create("cudajob", "linux", "x86_64", "ci",
                                   required_capabilities=["cuda"])
            # One builder covers BOTH the target and the capability → not reaped.
            reaped = await store.reap_unschedulable(
                schedulable_targets={("linux", "x86_64")},
                schedulable_platforms=set(),
                min_age_seconds=0,
                builder_offers=[({("linux", "x86_64")}, set(), {"cuda"})],
            )
            assert reaped == []
            assert (await store.get(j.id)).status == "pending"

        run(_t())

    def test_reap_unschedulable_noarch(self):
        from cvcpkg.platform import noarch_build_target
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            noarch = await store.create("anypkg", "any", "noarch", "ci")
            # With the reference build target registered, an 'any' job is fine.
            reaped = await store.reap_unschedulable(
                schedulable_targets={noarch_build_target()},
                schedulable_platforms=set(),
                min_age_seconds=0,
            )
            assert reaped == []
            assert (await store.get(noarch.id)).status == "pending"
            # With nothing registered, the same 'any' job is unschedulable.
            reaped2 = await store.reap_unschedulable(
                schedulable_targets=set(), schedulable_platforms=set(),
                min_age_seconds=0)
            assert {r.recipe_name for r in reaped2} == {"anypkg"}

        run(_t())

    def test_cancel_downstream(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            store = DbBuildJobStore()
            jobs = await store.create_dag(
                [{"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
                 {"recipe_name": "b", "platform": "linux", "arch": "x86_64",
                  "depends_on": [0]},
                 {"recipe_name": "c", "platform": "linux", "arch": "x86_64",
                  "depends_on": [1]}],
                dag_id="d1", submitted_by="ci")
            a = jobs[0]
            n = await store.cancel_downstream(a.id)
            assert n == 2  # b and c cancelled transitively
            statuses = {j.recipe_name: (await store.get(j.id)).status for j in jobs}
            assert statuses["b"] == "cancelled" and statuses["c"] == "cancelled"

        run(_t())

    def test_next_job_for_builder(self):
        from cvcpkg.server.db_stores import DbBuildJobStore, DbBuilderStore

        async def _t():
            builders = DbBuilderStore()
            store = DbBuildJobStore()
            b = await builders.register("bx", "linux", "x86_64", "root")
            assert await store.next_job_for_builder(b.id) is None
            j = await store.create("a", "linux", "x86_64", "ci")
            await store.dispatch(j.id, b.id)
            nxt = await store.next_job_for_builder(b.id)
            assert nxt.recipe_name == "a"

        run(_t())

    def test_logs_lifecycle_and_usage(self, tmp_path):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            logs = tmp_path / "logs"
            store = DbBuildJobStore()
            assert await store.append_log(9999, "x", logs_dir=logs) is None
            j = await store.create("a", "linux", "x86_64", "ci", org_slug="acme",
                                   dag_id="d1")
            info = await store.append_log(j.id, "hello ", logs_dir=logs)
            assert info.log_size_bytes == len("hello ")
            info2 = await store.append_log(j.id, "world", logs_dir=logs)
            assert info2.log_size_bytes == len("hello world")
            path = await store.get_log_path(j.id, logs_dir=logs)
            assert path is not None and path.read_text() == "hello world"
            assert await store.get_org_log_usage("acme") == len("hello world")
            # delete_log removes the file and clears metadata.
            assert await store.delete_log(j.id, logs_dir=logs) is True
            assert await store.get_log_path(j.id, logs_dir=logs) is None
            assert await store.delete_log(9999, logs_dir=logs) is False

        run(_t())

    def test_get_log_path_missing_file(self, tmp_path):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            logs = tmp_path / "logs"
            store = DbBuildJobStore()
            j = await store.create("a", "linux", "x86_64", "ci")
            # No log written yet → None.
            assert await store.get_log_path(j.id, logs_dir=logs) is None

        run(_t())

    def test_purge_old_logs_and_jobs(self, tmp_path):
        import datetime

        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _t():
            logs = tmp_path / "logs"
            store = DbBuildJobStore()
            j = await store.create("a", "linux", "x86_64", "ci")
            await store.append_log(j.id, "data", logs_dir=logs)
            await store.complete(j.id)
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
            await _set_job(j.id, finished_at=old)
            # purge_old_logs clears the log but keeps the row.
            n = await store.purge_old_logs(older_than_days=1, logs_dir=logs)
            assert n == 1
            assert await store.get_log_path(j.id, logs_dir=logs) is None
            assert (await store.get(j.id)) is not None

            # A second finished job, then purge_old_jobs deletes the row entirely.
            j2 = await store.create("b", "linux", "x86_64", "ci")
            await store.complete(j2.id)
            await _set_job(j2.id, finished_at=old)
            purged = await store.purge_old_jobs(older_than_days=1, logs_dir=logs)
            assert purged == 2  # both old finished jobs removed
            assert await store.get(j2.id) is None

        run(_t())


# ── DbRecipeStore ───────────────────────────────────────────────


class TestDbRecipeStore:
    def test_upload_get_list_delete(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _t():
            store = DbRecipeStore()
            info = await store.upload("zlib", "/bundles/zlib.tar", 1234, "alice",
                                      version="1.3.1", recipe_hash="abc")
            assert info.name == "zlib" and info.bundle_size == 1234
            # Upsert in place on the same (name, org).
            info2 = await store.upload("zlib", "/bundles/zlib2.tar", 5678, "bob",
                                       version="1.3.2")
            assert info2.id == info.id and info2.bundle_size == 5678
            assert (await store.get("zlib")).version == "1.3.2"
            assert await store.get("ghost") is None
            assert await store.get_bundle_path("zlib") == "/bundles/zlib2.tar"
            assert await store.get_bundle_path("ghost") is None
            # org filter + pagination
            await store.upload("png", "/bundles/png.tar", 10, "alice", org_slug="acme")
            base, total = await store.list_recipes(org_slug="")
            assert total == 1 and base[0].name == "zlib"
            org, ototal = await store.list_recipes(org_slug="acme")
            assert ototal == 1 and org[0].name == "png"
            allr, atotal = await store.list_recipes()
            assert atotal == 2
            assert await store.delete("zlib") is True
            assert await store.delete("zlib") is False

        run(_t())


# ── DbWebhookStore ──────────────────────────────────────────────


class TestDbWebhookStore:
    def test_register_get_secret_update_delete(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _t():
            store = DbWebhookStore()
            wh = await store.register("https://h.example/hook", ["publish"], "root",
                                      secret="s3cr3t")
            assert wh.url == "https://h.example/hook"
            assert wh.events == ["publish"]
            assert wh.active is True
            assert await store.get(wh.id) is not None
            assert await store.get(9999) is None
            assert await store.get_secret(wh.id) == "s3cr3t"
            assert await store.get_secret(9999) is None
            # register without a secret auto-generates one.
            wh2 = await store.register("https://h2.example/hook", ["*"], "root")
            assert await store.get_secret(wh2.id)
            upd = await store.update(wh.id, url="https://new.example",
                                     events=["yank"], active=False)
            assert upd.url == "https://new.example"
            assert upd.events == ["yank"]
            assert upd.active is False
            assert await store.update(9999, active=True) is None
            assert await store.delete(wh.id) is True
            assert await store.delete(wh.id) is False

        run(_t())

    def test_list_webhooks_filters(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _t():
            store = DbWebhookStore()
            a = await store.register("https://a.example", ["publish"], "root")
            await store.register("https://b.example", ["publish"], "root",
                                 org_slug="acme")
            await store.update(a.id, active=False)
            base, total = await store.list_webhooks(org_slug="")
            assert total == 1
            org, ototal = await store.list_webhooks(org_slug="acme")
            assert ototal == 1
            _, active_total = await store.list_webhooks(active_only=True)
            assert active_total == 1  # only b is still active
            page, all_total = await store.list_webhooks(limit=1)
            assert all_total == 2 and len(page) == 1

        run(_t())

    def test_record_delivery_and_failure_autodisable(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _t():
            store = DbWebhookStore()
            wh = await store.register("https://h.example", ["publish"], "root")
            # A few failures increment but do not disable.
            for _ in range(4):
                disabled = await store.record_failure(wh.id)
                assert disabled is False
            assert (await store.get(wh.id)).consecutive_failures == 4
            # A success resets the counter.
            await store.record_delivery(wh.id)
            assert (await store.get(wh.id)).consecutive_failures == 0
            # Reaching the threshold auto-disables.
            for _ in range(5):
                last = await store.record_failure(wh.id)
            assert last is True
            assert (await store.get(wh.id)).active is False
            assert await store.record_failure(9999) is False

        run(_t())

    def test_list_active_for_event(self):
        from cvcpkg.server.db_stores import DbWebhookStore

        async def _t():
            store = DbWebhookStore()
            await store.register("https://pub.example", ["publish"], "root")
            await store.register("https://star.example", ["*"], "root")
            yankonly = await store.register("https://yank.example", ["yank"], "root")
            await store.update(yankonly.id, active=False)  # inactive → excluded
            hooks = await store.list_active_for_event("publish")
            urls = {h.url for h in hooks}
            # explicit subscriber + wildcard subscriber, not the inactive one.
            assert urls == {"https://pub.example", "https://star.example"}
            # an org-scoped event only matches that org's hooks.
            assert await store.list_active_for_event("publish", org_slug="acme") == []

        run(_t())
