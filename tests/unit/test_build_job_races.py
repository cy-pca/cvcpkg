"""Every build-job status change is one conditional UPDATE.

dispatch(), pause(), resume(), pause_dag(), resume_dag(), cancel_dag(),
cancel_downstream() and reap_unschedulable() used to read a row and then write
it.  A competing writer that committed in between was silently overwritten:

* a cancel landing inside dispatch() came back as "dispatched" -- the builder
  then claimed it, built it and completed it, undoing the user's cancel;
* a claim landing inside pause() came back as "paused" while the builder was
  building it, and resume() then dispatched a second copy;
* the scheduler pushed ``job.dispatch`` to a builder for a job that had
  already left "pending", and counted it against the builder's slots.

Each race is driven two ways:

* deterministically, by committing the competing write inside the writer's
  own session -- right after its first read (the old window) or, for a writer
  whose first statement is already its conditional write, just before it;
* for real, on Postgres and SQLite, by holding the competing write open in
  another transaction while the writer runs and committing it afterwards: the
  writer has to wait on it and then re-check its predicate.

Runs on SQLite always and on Postgres when CVCPKG_TEST_POSTGRES_URL is set
(see tests/unit/_build_job_backends.py).
"""

from __future__ import annotations

import asyncio
import datetime
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from tests.unit._build_job_backends import BACKENDS, run_on  # noqa: E402

pytestmark = pytest.mark.parametrize("backend", BACKENDS)


async def _stores():
    from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

    return DbBuilderStore(), DbBuildJobStore()


async def _chain(jobs, dag_id="g"):
    """a <- b <- c, all pending."""
    return await jobs.create_dag(
        [
            {"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
            {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
        ],
        dag_id=dag_id,
        submitted_by="ci",
    )


def _land_inside_next_write(monkeypatch, competitor, *, after_reads: int = 1):
    """Commit *competitor* (an async callable) inside the next store session.

    It runs right after that session's *after_reads*-th read -- the window a
    read-then-write left open -- or just before its first write, whichever
    comes first.  It runs on its own session (the real get_session is restored
    first), so it commits independently.
    """
    import cvcpkg.server.db_stores as ds

    real_get_session = ds.get_session
    fired = {"done": False}
    reads = {"n": 0}

    @asynccontextmanager
    async def wrapped():
        async with real_get_session() as session:
            real_execute = session.execute

            async def land():
                fired["done"] = True
                monkeypatch.setattr(ds, "get_session", real_get_session)
                await competitor()

            async def execute(stmt, *a, **kw):
                is_read = getattr(stmt, "is_select", False)
                if not fired["done"] and not is_read:
                    await land()
                result = await real_execute(stmt, *a, **kw)
                if is_read:
                    reads["n"] += 1
                if not fired["done"] and reads["n"] >= after_reads:
                    await land()
                return result

            session.execute = execute
            yield session

    monkeypatch.setattr(ds, "get_session", wrapped)
    return fired


# ── Deterministic window races ───────────────────────────────────


class TestWindowRaces:
    def test_cancel_landing_inside_dispatch_stays_cancelled(self, backend, tmp_path, monkeypatch):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")

            async def cancel():
                assert (await jobs.cancel(j.id)).status == "cancelled"

            fired = _land_inside_next_write(monkeypatch, cancel)
            assert await jobs.dispatch(j.id, b.id) is None
            assert fired["done"]
            after = await jobs.get(j.id)
            assert after.status == "cancelled"
            assert after.builder_id is None
            # ...so the builder cannot claim it either.
            assert (await jobs.claim(j.id, b.id)).status == "cancelled"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_claim_landing_inside_pause_stays_running(self, backend, tmp_path, monkeypatch):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.dispatch(j.id, b.id)

            async def claim():
                assert (await jobs.claim(j.id, b.id)).status == "running"

            fired = _land_inside_next_write(monkeypatch, claim)
            info = await jobs.pause(j.id)
            assert fired["done"]
            assert info.status == "running"
            assert (await jobs.get(j.id)).status == "running"
            # Nothing to resume, so no second copy is dispatched.
            assert (await jobs.resume(j.id)).status == "running"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_cancel_landing_inside_resume_stays_cancelled(self, backend, tmp_path, monkeypatch):
        # A paused job can be force-cancelled by an operator only once it is
        # pending again; model the competing writer directly: a cancel that
        # commits between resume()'s read and its write must win.
        async def _t():
            from sqlalchemy import update

            from cvcpkg.server.db import BuildJobRow, get_session

            _, jobs = await _stores()
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.pause(j.id)

            async def cancel():
                async with get_session() as s:
                    await s.execute(
                        update(BuildJobRow).where(BuildJobRow.id == j.id).values(status="cancelled")
                    )

            fired = _land_inside_next_write(monkeypatch, cancel)
            info = await jobs.resume(j.id)
            assert fired["done"]
            assert info.status == "cancelled"

        run_on(backend, tmp_path, monkeypatch, _t)

    @pytest.mark.parametrize("op", ["cancel_dag", "pause_dag"])
    def test_claim_landing_inside_a_dag_op_stays_running(self, backend, op, tmp_path, monkeypatch):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            a, bb, c = await _chain(jobs, dag_id="d1")
            await jobs.dispatch(a.id, b.id)

            async def claim():
                assert (await jobs.claim(a.id, b.id)).status == "running"

            fired = _land_inside_next_write(monkeypatch, claim)
            count = await getattr(jobs, op)("d1")
            assert fired["done"]
            assert count == 2  # b and c; a was claimed first
            assert (await jobs.get(a.id)).status == "running"
            want = "cancelled" if op == "cancel_dag" else "paused"
            assert (await jobs.get(bb.id)).status == want
            assert (await jobs.get(c.id)).status == want

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_cancel_landing_inside_resume_dag_stays_cancelled(self, backend, tmp_path, monkeypatch):
        async def _t():
            from sqlalchemy import update

            from cvcpkg.server.db import BuildJobRow, get_session

            _, jobs = await _stores()
            a, b, c = await _chain(jobs, dag_id="d2")
            assert await jobs.pause_dag("d2") == 3

            async def cancel_a():
                async with get_session() as s:
                    await s.execute(
                        update(BuildJobRow).where(BuildJobRow.id == a.id).values(status="cancelled")
                    )

            fired = _land_inside_next_write(monkeypatch, cancel_a)
            assert await jobs.resume_dag("d2") == 2
            assert fired["done"]
            assert (await jobs.get(a.id)).status == "cancelled"
            assert (await jobs.get(b.id)).status == "pending"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_claim_landing_inside_the_cascade_stays_running(self, backend, tmp_path, monkeypatch):
        # An unregistered drainer selects by platform, so it can claim a
        # dependent while the cascade for its failed upstream is running.
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            a, bb, c = await _chain(jobs)
            await jobs.claim(a.id, b.id)
            await jobs.fail(a.id, error_message="boom")

            async def claim_b():
                assert (await jobs.claim(bb.id, None, claimant="drainer")).status == "running"

            # Land after the cascade has read b (its 2nd read: the edge list,
            # then the row) -- or before its first write, whichever is first.
            fired = _land_inside_next_write(monkeypatch, claim_b, after_reads=2)
            cascaded = await jobs.cancel_downstream(a.id)
            assert fired["done"]
            assert cascaded == 1  # only c
            assert (await jobs.get(bb.id)).status == "running"
            assert (await jobs.get(c.id)).status == "cancelled"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_claim_landing_inside_unschedulable_reap_stays_running(
        self, backend, tmp_path, monkeypatch
    ):
        async def _t():
            from sqlalchemy import update

            from cvcpkg.server.db import BuildJobRow, get_session

            _, jobs = await _stores()
            j = await jobs.create("a", "haiku", "x86_64", "ci")
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
            async with get_session() as s:
                await s.execute(
                    update(BuildJobRow).where(BuildJobRow.id == j.id).values(submitted_at=old)
                )

            async def claim():
                assert (await jobs.claim(j.id, None, claimant="drainer")).status == "running"

            fired = _land_inside_next_write(monkeypatch, claim)
            reaped = await jobs.reap_unschedulable(set(), set(), min_age_seconds=60)
            assert fired["done"]
            assert reaped == []  # so the caller cascades nothing
            assert (await jobs.get(j.id)).status == "running"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_unschedulable_reap_still_reaps_an_untouched_job(self, backend, tmp_path, monkeypatch):
        async def _t():
            from sqlalchemy import update

            from cvcpkg.server.db import BuildJobRow, get_session

            _, jobs = await _stores()
            j = await jobs.create("a", "haiku", "x86_64", "ci")
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
            async with get_session() as s:
                await s.execute(
                    update(BuildJobRow).where(BuildJobRow.id == j.id).values(submitted_at=old)
                )
            reaped = await jobs.reap_unschedulable(set(), set(), min_age_seconds=60)
            assert [r.id for r in reaped] == [j.id]
            assert reaped[0].status == "unschedulable"
            assert "haiku/x86_64" in reaped[0].error_message

        run_on(backend, tmp_path, monkeypatch, _t)


# ── Real concurrency: the competing write is held open ───────────


async def _hold_open_write(job_id: int, values: dict):
    """Start a transaction on its own connection that writes *values* to the
    job and keeps it uncommitted (holding the row / write lock).  Returns
    ``(conn, trans)``; the caller commits and closes."""
    from sqlalchemy import update

    from cvcpkg.server import db as dbmod
    from cvcpkg.server.db import BuildJobRow

    conn = await dbmod._engine.connect()
    trans = await conn.begin()
    await conn.execute(update(BuildJobRow).where(BuildJobRow.id == job_id).values(**values))
    return conn, trans


async def _race(writer, job_id: int, competing: dict):
    """Run *writer* while *competing* is held uncommitted, then commit it."""
    conn, trans = await _hold_open_write(job_id, competing)
    try:
        task = asyncio.ensure_future(writer())
        await asyncio.sleep(0.5)
        # The writer cannot have finished: it is waiting on the open write.
        assert not task.done()
        await trans.commit()
    finally:
        await conn.close()
    return await asyncio.wait_for(task, timeout=20)


class TestHeldOpenRaces:
    def test_dispatch_waits_for_a_cancel_and_then_dispatches_nothing(
        self, backend, tmp_path, monkeypatch
    ):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            now = datetime.datetime.now(datetime.timezone.utc)
            result = await _race(
                lambda: jobs.dispatch(j.id, b.id),
                j.id,
                {"status": "cancelled", "finished_at": now},
            )
            assert result is None
            assert (await jobs.get(j.id)).status == "cancelled"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_pause_waits_for_a_claim_and_then_leaves_it_running(
        self, backend, tmp_path, monkeypatch
    ):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.dispatch(j.id, b.id)
            now = datetime.datetime.now(datetime.timezone.utc)
            info = await _race(
                lambda: jobs.pause(j.id),
                j.id,
                {"status": "running", "builder_id": b.id, "started_at": now},
            )
            assert info.status == "running"
            assert (await jobs.get(j.id)).status == "running"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_cancel_dag_waits_for_a_claim_and_spares_it(self, backend, tmp_path, monkeypatch):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            a, bb, c = await _chain(jobs, dag_id="d3")
            now = datetime.datetime.now(datetime.timezone.utc)
            count = await _race(
                lambda: jobs.cancel_dag("d3"),
                a.id,
                {"status": "running", "builder_id": b.id, "started_at": now},
            )
            assert count == 2
            assert (await jobs.get(a.id)).status == "running"
            assert (await jobs.get(c.id)).status == "cancelled"

        run_on(backend, tmp_path, monkeypatch, _t)


# ── The scheduler honours dispatch()'s answer ────────────────────


class TestSchedulerHonoursDispatch:
    def test_job_cancelled_after_listing_is_not_pushed_or_counted(
        self, backend, tmp_path, monkeypatch
    ):
        import cvcpkg.server.app as app_mod

        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            keep = await jobs.create("k", "linux", "x86_64", "ci")

            real_ready = jobs.find_ready_jobs

            async def ready_then_cancel():
                listed = await real_ready()
                await jobs.cancel(j.id)  # lands between listing and dispatch
                return listed

            monkeypatch.setattr(jobs, "find_ready_jobs", ready_then_cancel)
            monkeypatch.setattr(app_mod, "_use_db", True)
            monkeypatch.setattr(app_mod, "_db_builders", builders)
            monkeypatch.setattr(app_mod, "_db_build_jobs", jobs)
            sent = AsyncMock(return_value=True)
            monkeypatch.setattr(app_mod, "_ws_send", sent)

            ticks = {"n": 0}

            async def one_tick(_secs):
                ticks["n"] += 1
                if ticks["n"] > 1:
                    raise asyncio.CancelledError

            monkeypatch.setattr(asyncio, "sleep", one_tick)
            with pytest.raises(asyncio.CancelledError):
                await app_mod._build_scheduler_loop()

            assert (await jobs.get(j.id)).status == "cancelled"
            assert (await jobs.get(j.id)).builder_id is None
            pushed = [c.args[1]["job"]["id"] for c in sent.await_args_list]
            assert j.id not in pushed
            # The untouched job is still dispatched and pushed as before...
            assert pushed == [keep.id]
            assert (await jobs.get(keep.id)).status == "dispatched"
            # ...and only it is counted against the builder.
            assert (await builders.get(b.id)).current_jobs == 1

        run_on(backend, tmp_path, monkeypatch, _t)


# ── The reporter of a complete/fail must still hold the job ──────


class TestReporterMustHoldTheJob:
    def test_stale_report_from_a_previous_holder_is_refused(self, backend, tmp_path, monkeypatch):
        from cvcpkg.server.models import BuildJobNotActiveError, BuildJobNotHeldError

        async def _t():
            builders, jobs = await _stores()
            ba = await builders.register("ba", "linux", "x86_64", "root")
            bb = await builders.register("bb", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            # First attempt: dispatched to A, then paused and resumed; the
            # scheduler hands the second attempt to B, which claims it.
            await jobs.dispatch(j.id, ba.id)
            await jobs.pause(j.id)
            await jobs.resume(j.id)
            await jobs.dispatch(j.id, bb.id)
            await jobs.claim(j.id, bb.id)

            for verb in ("complete", "fail"):
                with pytest.raises(BuildJobNotHeldError) as ei:
                    await getattr(jobs, verb)(j.id, builder_id=ba.id)
                assert not ei.value.is_repeat
                assert f"builder #{bb.id}" in str(ei.value)
                assert f"builder #{ba.id}" in str(ei.value)
                assert isinstance(ei.value, BuildJobNotActiveError)  # same refusal path
            after = await jobs.get(j.id)
            assert after.status == "running"
            assert after.builder_id == bb.id

            done = await jobs.complete(j.id, builder_id=bb.id, result_archive_url="/v1/p/a")
            assert done.status == "succeeded"

            # Once finished, a stale report is judged on status, as before: a
            # matching outcome is a harmless repeat, a contradicting one a
            # conflict -- never NotHeld.
            with pytest.raises(BuildJobNotActiveError) as ei:
                await jobs.complete(j.id, builder_id=ba.id)
            assert ei.value.is_repeat and not isinstance(ei.value, BuildJobNotHeldError)
            with pytest.raises(BuildJobNotActiveError) as ei:
                await jobs.fail(j.id, builder_id=ba.id)
            assert not ei.value.is_repeat and not isinstance(ei.value, BuildJobNotHeldError)

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_claimant_is_checked_for_an_unregistered_worker(self, backend, tmp_path, monkeypatch):
        from cvcpkg.server.models import BuildJobNotHeldError

        async def _t():
            _, jobs = await _stores()
            j = await jobs.create("a", "macos", "arm64", "ci")
            await jobs.claim(j.id, None, claimant="gha-run-1")
            with pytest.raises(BuildJobNotHeldError):
                await jobs.fail(j.id, claimant="gha-run-0", error_message="stale")
            assert (await jobs.get(j.id)).status == "running"
            assert (await jobs.complete(j.id, claimant=" gha-run-1 ")).status == "succeeded"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_report_from_a_deleted_holder_still_lands(self, backend, tmp_path, monkeypatch):
        # An admin deleting a builder mid-job leaves the job with no holder
        # (builder_id SET NULL).  The builder's own report still names its old
        # id; refusing it would strand the job until the build timeout.
        from cvcpkg.server.models import BuildJobNotHeldError

        async def _t():
            builders, jobs = await _stores()
            ba = await builders.register("ba", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.dispatch(j.id, ba.id)
            await jobs.claim(j.id, ba.id)
            assert await builders.unregister(ba.id)
            orphan = await jobs.get(j.id)
            assert orphan.status == "running" and orphan.builder_id is None

            # A claimant-only report does not match a job claimed by id.
            with pytest.raises(BuildJobNotHeldError):
                await jobs.fail(j.id, claimant="someone-else", error_message="x")
            done = await jobs.complete(j.id, builder_id=ba.id, result_archive_url="/v1/p/a")
            assert done.status == "succeeded"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_a_claimant_recorded_with_the_claim_must_match_on_an_orphan(
        self, backend, tmp_path, monkeypatch
    ):
        from cvcpkg.server.models import BuildJobNotHeldError

        async def _t():
            builders, jobs = await _stores()
            ba = await builders.register("ba", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, ba.id, claimant="ba-run-7")
            assert await builders.unregister(ba.id)
            with pytest.raises(BuildJobNotHeldError):
                await jobs.fail(j.id, builder_id=ba.id, claimant="ba-run-6")
            assert (await jobs.get(j.id)).status == "running"
            done = await jobs.fail(j.id, builder_id=ba.id, claimant="ba-run-7")
            assert done.status == "failed"

        run_on(backend, tmp_path, monkeypatch, _t)

    def test_a_report_naming_nobody_is_checked_on_status_alone(
        self, backend, tmp_path, monkeypatch
    ):
        # Older builders send no reporter: unchanged behaviour for them.
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            assert (await jobs.complete(j.id)).status == "succeeded"

        run_on(backend, tmp_path, monkeypatch, _t)
