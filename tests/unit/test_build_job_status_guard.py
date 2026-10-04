"""A build job can be completed or failed only while it is active.

Before this guard ``complete`` and ``fail`` overwrote a job's status whatever
it was, so the last report won.  When one job ran twice on a builder, the copy
that finished second decided the outcome, and a late ``fail`` on a job that
had already succeeded flipped it to failed and cascade-cancelled every
dependent waiting on it.

The rule these tests pin down:

* only a *dispatched* or *running* job can be completed or failed;
* a report for any other job changes nothing -- not the status, not
  ``finished_at``, ``error_message`` or ``result_archive_url`` -- and does not
  cascade; over HTTP it is a 409, except that
* a repeat of the outcome the job already has (a retry after a lost response,
  the second copy of a duplicated job) is a harmless 200 no-op with no second
  webhook, cascade or audit entry.

Covered at the store, the HTTP endpoints and the builder WebSocket.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")

from fastapi.testclient import TestClient  # noqa: E402

from cvcpkg.server.models import BuildJobNotActiveError, TokenRole  # noqa: E402

# ── Store ───────────────────────────────────────────────────────


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'guard.db'}"
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


async def _stores():
    from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

    return DbBuilderStore(), DbBuildJobStore()


async def _dag(jobs_store, dag_id="g"):
    """a <- b <- c, all pending."""
    return await jobs_store.create_dag(
        [
            {"recipe_name": "a", "platform": "linux", "arch": "x86_64"},
            {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
            {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
        ],
        dag_id=dag_id,
        submitted_by="ci",
    )


def _frozen(info):
    """The fields a refused report must not touch.

    ``finished_at`` is compared without its tzinfo: a store method that set it
    in-session hands back the aware value, a fresh read from SQLite a naive one.
    """
    finished = info.finished_at.replace(tzinfo=None) if info.finished_at else None
    return (info.status, finished, info.error_message, info.result_archive_url)


@pytest.mark.usefixtures("store_env")
class TestStoreGuard:
    def test_running_job_completes_and_fails(self):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            done = await jobs.complete(j.id, result_archive_url="/v1/packages/a")
            assert done.status == "succeeded"
            assert done.finished_at is not None
            assert (await builders.get(b.id)).current_jobs == 0

            j2 = await jobs.create("b", "linux", "x86_64", "ci")
            await jobs.claim(j2.id, b.id)
            failed = await jobs.fail(j2.id, error_message="broke")
            assert failed.status == "failed"
            assert failed.error_message == "broke"

        run(_t())

    def test_dispatched_job_can_be_failed_and_completed(self):
        # The scheduler fails the dispatched-but-unclaimed jobs of a builder
        # that went offline, so ``dispatched`` must stay finishable.
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.dispatch(j.id, b.id)
            failed = await jobs.fail(j.id, error_message="builder went offline")
            assert failed.status == "failed"
            assert (await builders.get(b.id)).current_jobs == 0

            j2 = await jobs.create("b", "linux", "x86_64", "ci")
            await jobs.dispatch(j2.id, b.id)
            assert (await jobs.complete(j2.id)).status == "succeeded"

        run(_t())

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_pending_job_is_refused(self, verb):
        # Nobody was handed this job, so nobody can finish it.
        async def _t():
            _, jobs = await _stores()
            j = await jobs.create("a", "linux", "x86_64", "ci")
            with pytest.raises(BuildJobNotActiveError) as ei:
                await getattr(jobs, verb)(j.id)
            assert ei.value.status == "pending"
            assert not ei.value.is_repeat
            after = await jobs.get(j.id)
            assert after.status == "pending"
            assert after.finished_at is None

        run(_t())

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_paused_job_is_refused(self, verb):
        async def _t():
            _, jobs = await _stores()
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.pause(j.id)
            with pytest.raises(BuildJobNotActiveError) as ei:
                await getattr(jobs, verb)(j.id)
            assert ei.value.status == "paused"
            assert (await jobs.get(j.id)).status == "paused"

        run(_t())

    def test_late_fail_after_complete_changes_nothing(self):
        """The production failure: a duplicate copy fails after the job succeeded."""

        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            dag = await _dag(jobs)
            await jobs.claim(dag[0].id, b.id)
            done = await jobs.complete(dag[0].id, result_archive_url="/v1/packages/a")
            with pytest.raises(BuildJobNotActiveError) as ei:
                await jobs.fail(dag[0].id, error_message="duplicate copy: publish 409")
            exc = ei.value
            assert exc.status == "succeeded"
            assert exc.attempted == "failed"
            assert not exc.is_repeat
            assert exc.info is not None and exc.info.status == "succeeded"
            assert "succeeded" in str(exc)
            after = await jobs.get(dag[0].id)
            assert _frozen(after) == _frozen(done)
            # b became ready on the success and nothing cancelled it.
            assert (await jobs.get(dag[1].id)).status == "pending"
            assert {r.id for r in await jobs.find_ready_jobs()} == {dag[1].id}

        run(_t())

    def test_complete_after_fail_changes_nothing(self):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            failed = await jobs.fail(j.id, error_message="cmake failed")
            with pytest.raises(BuildJobNotActiveError) as ei:
                await jobs.complete(j.id, result_archive_url="/v1/packages/a")
            assert ei.value.status == "failed"
            assert ei.value.attempted == "succeeded"
            assert not ei.value.is_repeat
            after = await jobs.get(j.id)
            assert _frozen(after) == _frozen(failed)
            assert after.error_message == "cmake failed"
            assert after.result_archive_url in (None, "")

        run(_t())

    def test_repeat_complete_is_flagged_as_repeat_and_keeps_first_report(self):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            done = await jobs.complete(j.id, result_archive_url="/v1/packages/a")
            with pytest.raises(BuildJobNotActiveError) as ei:
                await jobs.complete(j.id, result_archive_url="/v1/packages/other")
            assert ei.value.is_repeat
            assert ei.value.info.status == "succeeded"
            assert _frozen(await jobs.get(j.id)) == _frozen(done)

        run(_t())

    def test_repeat_fail_is_flagged_as_repeat_and_keeps_first_report(self):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            failed = await jobs.fail(j.id, error_message="first")
            with pytest.raises(BuildJobNotActiveError) as ei:
                await jobs.fail(j.id, error_message="second")
            assert ei.value.is_repeat
            assert _frozen(await jobs.get(j.id)) == _frozen(failed)

        run(_t())

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_cancelled_job_is_refused(self, verb):
        async def _t():
            _, jobs = await _stores()
            j = await jobs.create("a", "linux", "x86_64", "ci")
            cancelled = await jobs.cancel(j.id)
            assert cancelled.status == "cancelled"
            with pytest.raises(BuildJobNotActiveError) as ei:
                await getattr(jobs, verb)(j.id)
            assert ei.value.status == "cancelled"
            assert not ei.value.is_repeat
            assert _frozen(await jobs.get(j.id)) == _frozen(cancelled)

        run(_t())

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_force_cancelled_running_job_is_refused(self, verb):
        # An operator force-cancels a stuck job; the builder's report that
        # trickles in afterwards must not resurrect or re-fail it.
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            dag = await _dag(jobs)
            await jobs.claim(dag[0].id, b.id)
            cancelled = await jobs.cancel(dag[0].id, force=True)
            assert cancelled.status == "cancelled"
            with pytest.raises(BuildJobNotActiveError):
                await getattr(jobs, verb)(dag[0].id)
            assert _frozen(await jobs.get(dag[0].id)) == _frozen(cancelled)
            assert (await builders.get(b.id)).current_jobs == 0

        run(_t())

    def test_cancel_after_complete_is_a_noop(self):
        # The other direction: cancel never touches a finished job.
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            await jobs.complete(j.id)
            assert (await jobs.cancel(j.id)).status == "succeeded"
            assert (await jobs.cancel(j.id, force=True)).status == "succeeded"

        run(_t())

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_timed_out_job_is_refused(self, verb):
        import datetime

        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci", timeout_seconds=1)
            await jobs.claim(j.id, b.id)
            from sqlalchemy import update

            from cvcpkg.server.db import BuildJobRow, get_session

            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
            async with get_session() as session:
                await session.execute(
                    update(BuildJobRow).where(BuildJobRow.id == j.id).values(started_at=old)
                )
            reaped = await jobs.reap_timed_out()
            assert [r.id for r in reaped] == [j.id]
            with pytest.raises(BuildJobNotActiveError) as ei:
                await getattr(jobs, verb)(j.id)
            assert ei.value.status == "timed_out"
            assert (await jobs.get(j.id)).status == "timed_out"

        run(_t())

    def test_reaper_fail_loses_to_a_report_that_landed_first(self):
        """The scheduler lists a builder's active jobs, then fails them.

        A job that completed in between must keep its success: the reaper's
        fail is refused rather than overwriting it.
        """

        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            listed = await jobs.list_active_by_builder(b.id)
            assert [x.id for x in listed] == [j.id]
            await jobs.complete(j.id)  # lands between list and fail
            with pytest.raises(BuildJobNotActiveError):
                await jobs.fail(j.id, error_message="builder went offline")
            assert (await jobs.get(j.id)).status == "succeeded"

        run(_t())

    def test_missing_job_is_still_none(self):
        async def _t():
            _, jobs = await _stores()
            assert await jobs.complete(9999) is None
            assert await jobs.fail(9999) is None

        run(_t())


# ── HTTP endpoints ──────────────────────────────────────────────


@pytest.fixture()
def http_env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'guard-http.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("guard-admin", TokenRole.admin)
        pub_raw = await store.create("guard-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _submit_dag(client, tok, dag_id):
    resp = client.post(
        "/v1/builds/dag",
        json={
            "dag_id": dag_id,
            "jobs": [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "depends_on": []},
                {"recipe_name": "b", "platform": "linux", "arch": "x86_64", "depends_on": [0]},
                {"recipe_name": "c", "platform": "linux", "arch": "x86_64", "depends_on": [1]},
            ],
        },
        headers=_h(tok),
    )
    assert resp.status_code == 200, resp.text
    return [j["id"] for j in resp.json()["jobs"]]


def _claim(client, tok, job_id, claimant="worker-1"):
    resp = client.post(f"/v1/builds/{job_id}/claim", json={"claimant": claimant}, headers=_h(tok))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "running"


def _get(client, tok, job_id):
    resp = client.get(f"/v1/builds/{job_id}", headers=_h(tok))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _audit_count(client, admin_tok, action, job_id):
    resp = client.get(
        "/v1/audit", params={"action": action, "target": str(job_id)}, headers=_h(admin_tok)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["total"]


class TestHttpGuard:
    def test_late_fail_after_complete_is_409_and_does_not_cascade(self, http_env, caplog):
        caplog.set_level("INFO", logger="cvcpkg.server")
        client, admin, pub = http_env
        a, b, c = _submit_dag(client, pub, "late-fail")
        _claim(client, pub, a)
        ok = client.post(
            f"/v1/builds/{a}/complete",
            json={"result_archive_url": "/v1/packages/a"},
            headers=_h(pub),
        )
        assert ok.status_code == 200, ok.text
        before = _get(client, pub, a)

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            late = client.post(
                f"/v1/builds/{a}/fail",
                json={"error_message": "duplicate copy failed"},
                headers=_h(pub),
            )
        assert late.status_code == 409, late.text
        assert "succeeded" in late.json()["detail"]
        emit.assert_not_called()
        # Refused, so not audited -- but not invisible either.
        assert any(
            r.levelname == "WARNING" and f"build job {a}: failed report refused" in r.getMessage()
            for r in caplog.records
        )

        after = _get(client, pub, a)
        assert after["status"] == "succeeded"
        assert after["finished_at"] == before["finished_at"]
        assert after["error_message"] == before["error_message"]
        assert after["result_archive_url"] == "/v1/packages/a"
        # The dependents were NOT cascade-cancelled.
        assert _get(client, pub, b)["status"] in ("pending", "dispatched")
        assert _get(client, pub, c)["status"] == "pending"
        # Nothing was audited for the refused report.
        assert _audit_count(client, admin, "build_fail", a) == 0

    def test_complete_after_fail_is_409(self, http_env):
        client, admin, pub = http_env
        a, b, c = _submit_dag(client, pub, "complete-after-fail")
        _claim(client, pub, a)
        failed = client.post(
            f"/v1/builds/{a}/fail", json={"error_message": "cmake failed"}, headers=_h(pub)
        )
        assert failed.status_code == 200, failed.text
        # The real fail cascades as before.
        assert _get(client, pub, b)["status"] == "cancelled"
        assert _get(client, pub, c)["status"] == "cancelled"

        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            late = client.post(
                f"/v1/builds/{a}/complete",
                json={"result_archive_url": "/v1/packages/a"},
                headers=_h(pub),
            )
        assert late.status_code == 409, late.text
        assert "failed" in late.json()["detail"]
        emit.assert_not_called()
        after = _get(client, pub, a)
        assert after["status"] == "failed"
        assert after["error_message"] == "cmake failed"
        assert _audit_count(client, admin, "build_complete", a) == 0

    def test_repeat_complete_is_200_noop(self, http_env):
        client, admin, pub = http_env
        (a, *_rest) = _submit_dag(client, pub, "repeat-complete")
        _claim(client, pub, a)
        first = client.post(
            f"/v1/builds/{a}/complete",
            json={"result_archive_url": "/v1/packages/a"},
            headers=_h(pub),
        )
        assert first.status_code == 200, first.text
        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            again = client.post(
                f"/v1/builds/{a}/complete",
                json={"result_archive_url": "/v1/packages/elsewhere"},
                headers=_h(pub),
            )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "succeeded"
        assert again.json()["result_archive_url"] == "/v1/packages/a"
        assert again.json()["finished_at"] == first.json()["finished_at"]
        emit.assert_not_called()  # no second build.completed
        assert _audit_count(client, admin, "build_complete", a) == 1

    def test_repeat_fail_is_200_noop_without_a_second_cascade(self, http_env):
        client, admin, pub = http_env
        a, b, c = _submit_dag(client, pub, "repeat-fail")
        _claim(client, pub, a)
        first = client.post(
            f"/v1/builds/{a}/fail", json={"error_message": "first"}, headers=_h(pub)
        )
        assert first.status_code == 200, first.text
        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            again = client.post(
                f"/v1/builds/{a}/fail", json={"error_message": "second"}, headers=_h(pub)
            )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "failed"
        assert again.json()["error_message"] == "first"
        emit.assert_not_called()  # no second build.failed
        assert _audit_count(client, admin, "build_fail", a) == 1

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_unclaimed_pending_job_is_409(self, http_env, verb):
        client, _admin, pub = http_env
        a, b, _c = _submit_dag(client, pub, f"pending-{verb}")
        resp = client.post(f"/v1/builds/{a}/{verb}", json={}, headers=_h(pub))
        assert resp.status_code == 409, resp.text
        assert "pending" in resp.json()["detail"]
        assert _get(client, pub, a)["status"] == "pending"
        assert _get(client, pub, b)["status"] == "pending"  # no cascade

    @pytest.mark.parametrize("verb", ["complete", "fail"])
    def test_report_after_force_cancel_is_409(self, http_env, verb):
        client, _admin, pub = http_env
        a, b, c = _submit_dag(client, pub, f"force-cancel-{verb}")
        _claim(client, pub, a)
        cancel = client.post(f"/v1/builds/{a}/cancel", params={"force": True}, headers=_h(pub))
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "cancelled"
        late = client.post(f"/v1/builds/{a}/{verb}", json={}, headers=_h(pub))
        assert late.status_code == 409, late.text
        assert "cancelled" in late.json()["detail"]
        assert _get(client, pub, a)["status"] == "cancelled"

    def test_cancel_after_complete_is_noop(self, http_env):
        client, _admin, pub = http_env
        a, b, _c = _submit_dag(client, pub, "cancel-after-complete")
        _claim(client, pub, a)
        assert client.post(f"/v1/builds/{a}/complete", json={}, headers=_h(pub)).status_code == 200
        resp = client.post(f"/v1/builds/{a}/cancel", params={"force": True}, headers=_h(pub))
        assert resp.status_code == 200, resp.text
        assert resp.json()["message"] == "no-op"
        assert _get(client, pub, a)["status"] == "succeeded"
        assert _get(client, pub, b)["status"] in ("pending", "dispatched")

    def test_not_found_is_still_404(self, http_env):
        client, _admin, pub = http_env
        assert client.post("/v1/builds/9999/complete", json={}, headers=_h(pub)).status_code == 404
        assert client.post("/v1/builds/9999/fail", json={}, headers=_h(pub)).status_code == 404


# ── Builder WebSocket ───────────────────────────────────────────


class TestWebSocketGuard:
    def _builder(self, client, tok):
        resp = client.post(
            "/v1/builders/register",
            json={"name": "ws-guard", "platform": "linux", "arch": "x86_64"},
            headers=_h(tok),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    def test_late_fail_after_complete_is_refused_without_cascade(self, http_env):
        client, _admin, pub = http_env
        bid = self._builder(client, pub)
        a, b, c = _submit_dag(client, pub, "ws-late-fail")
        with client.websocket_connect(f"/v1/builders/{bid}/ws?token={pub}") as ws:
            ws.send_json({"type": "job.claim", "job_id": a})
            assert ws.receive_json()["status"] == "running"
            ws.send_json({"type": "job.complete", "job_id": a, "archive_url": "/v1/packages/a"})
            ack = ws.receive_json()
            assert ack["type"] == "job.complete_ack"
            assert ack["status"] == "succeeded"
            assert "noop" not in ack

            # A repeat is a harmless no-op.
            ws.send_json({"type": "job.complete", "job_id": a, "archive_url": "/v1/packages/a"})
            ack = ws.receive_json()
            assert ack["type"] == "job.complete_ack"
            assert ack["status"] == "succeeded"
            assert ack["noop"] is True
            assert ack["conflict"] is False

            # A late fail is refused and does not cascade.
            ws.send_json({"type": "job.fail", "job_id": a, "error": "duplicate copy"})
            ack = ws.receive_json()
            assert ack["type"] == "job.fail_ack"
            assert ack["status"] == "succeeded"
            assert ack["noop"] is True
            assert ack["conflict"] is True
            assert "succeeded" in ack["detail"]
        assert _get(client, pub, a)["status"] == "succeeded"
        assert _get(client, pub, b)["status"] in ("pending", "dispatched")
        assert _get(client, pub, c)["status"] == "pending"

    def test_complete_after_fail_is_refused(self, http_env):
        client, _admin, pub = http_env
        bid = self._builder(client, pub)
        a, _b, _c = _submit_dag(client, pub, "ws-complete-after-fail")
        with client.websocket_connect(f"/v1/builders/{bid}/ws?token={pub}") as ws:
            ws.send_json({"type": "job.claim", "job_id": a})
            ws.receive_json()
            ws.send_json({"type": "job.fail", "job_id": a, "error": "OOM killed"})
            assert ws.receive_json()["status"] == "failed"
            ws.send_json({"type": "job.complete", "job_id": a, "archive_url": "/v1/packages/a"})
            ack = ws.receive_json()
            assert ack["type"] == "job.complete_ack"
            assert ack["status"] == "failed"
            assert ack["noop"] is True
            assert ack["conflict"] is True
            # The socket survives a refused report.
            ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 0})
            assert ws.receive_json()["type"] == "heartbeat_ack"
        after = _get(client, pub, a)
        assert after["status"] == "failed"
        assert after["error_message"] == "OOM killed"


# ── The other writers: timeout reaper, force-cancel, offline reaper ─────────


def _land_complete_mid_write(monkeypatch, job_id):
    """Commit a builder's ``complete`` for *job_id* inside another writer.

    Wraps the next store session so that, right after that writer's first
    statement (the SELECT that sees the job still ``running``), the success
    commits on a separate connection -- before the writer's own UPDATE.  A
    single-worker server opens exactly that window at every ``await``.  (A
    writer that starts with its conditional UPDATE has no such window; the
    success then lands just before it.)
    """
    from contextlib import asynccontextmanager

    import cvcpkg.server.db_stores as ds

    real_get_session = ds.get_session
    fired = {"done": False}

    @asynccontextmanager
    async def wrapped():
        async with real_get_session() as session:
            real_execute = session.execute

            async def land():
                fired["done"] = True
                monkeypatch.setattr(ds, "get_session", real_get_session)
                landed = await asyncio.ensure_future(
                    ds.DbBuildJobStore().complete(job_id, result_archive_url="/v1/packages/a")
                )
                assert landed.status == "succeeded"

            async def execute(stmt, *a, **kw):
                # A writer whose first statement is already its (conditional)
                # write has no read-then-write window: land just before it.
                if not fired["done"] and not getattr(stmt, "is_select", False):
                    await land()
                result = await real_execute(stmt, *a, **kw)
                if not fired["done"]:
                    await land()  # right after the read that saw "running"
                return result

            session.execute = execute
            yield session

    monkeypatch.setattr(ds, "get_session", wrapped)
    return fired


@pytest.mark.usefixtures("store_env")
class TestOtherWritersRespectAFinishedJob:
    def test_timeout_reaper_does_not_overwrite_a_success_that_lands_mid_reap(self, monkeypatch):
        import datetime

        from sqlalchemy import update

        from cvcpkg.server.db import BuildJobRow, get_session

        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            dag = await _dag(jobs)
            await jobs.claim(dag[0].id, b.id)
            old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
            async with get_session() as session:
                await session.execute(
                    update(BuildJobRow).where(BuildJobRow.id == dag[0].id).values(started_at=old)
                )
            fired = _land_complete_mid_write(monkeypatch, dag[0].id)
            reaped = await jobs.reap_timed_out()
            assert fired["done"]
            # The success stands; the reaper reports nothing, so its caller
            # cascades nothing.
            assert reaped == []
            assert (await jobs.get(dag[0].id)).status == "succeeded"

        run(_t())

    def test_force_cancel_does_not_overwrite_a_success_that_lands_mid_cancel(self, monkeypatch):
        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            j = await jobs.create("a", "linux", "x86_64", "ci")
            await jobs.claim(j.id, b.id)
            fired = _land_complete_mid_write(monkeypatch, j.id)
            info = await jobs.cancel(j.id, force=True)
            assert fired["done"]
            # cancel answers with the job as it stands (the endpoint then
            # treats it as a no-op and cascades nothing).
            assert info.status == "succeeded"
            assert (await jobs.get(j.id)).status == "succeeded"

        run(_t())

    def test_scheduler_offline_reaper_skips_a_job_that_finished_after_listing(self, monkeypatch):
        """Drive one tick of the real scheduler loop through the race."""
        import cvcpkg.server.app as app_mod

        async def _t():
            builders, jobs = await _stores()
            b = await builders.register("bx", "linux", "x86_64", "root")
            dag = await _dag(jobs)
            await jobs.claim(dag[0].id, b.id)
            # A second job the offline builder really did abandon.
            x = await jobs.create("x", "linux", "x86_64", "ci")
            await jobs.claim(x.id, b.id)

            async def reap_stale(max_age_seconds=180):
                return [await builders.get(b.id)]

            real_list = jobs.list_active_by_builder

            async def list_then_complete(builder_id):
                listed = await real_list(builder_id)
                await jobs.complete(dag[0].id)  # lands between list and fail
                # The finished job first, so a refused fail that escaped
                # would abort the tick before the abandoned one is handled.
                return sorted(listed, key=lambda j: j.id != dag[0].id)

            monkeypatch.setattr(builders, "reap_stale", reap_stale)
            monkeypatch.setattr(jobs, "list_active_by_builder", list_then_complete)
            cascade = AsyncMock(wraps=jobs.cancel_downstream)
            monkeypatch.setattr(jobs, "cancel_downstream", cascade)
            monkeypatch.setattr(app_mod, "_use_db", True)
            monkeypatch.setattr(app_mod, "_db_builders", builders)
            monkeypatch.setattr(app_mod, "_db_build_jobs", jobs)

            ticks = {"n": 0}

            async def one_tick(_secs):
                ticks["n"] += 1
                if ticks["n"] > 1:
                    raise asyncio.CancelledError

            monkeypatch.setattr(asyncio, "sleep", one_tick)
            emit = AsyncMock()
            monkeypatch.setattr(app_mod, "emit_webhook_event", emit)
            with pytest.raises(asyncio.CancelledError):
                await app_mod._build_scheduler_loop()

            assert (await jobs.get(dag[0].id)).status == "succeeded"
            failed_events = [
                c.args[1]["job_id"] for c in emit.call_args_list if c.args[0] == "build.failed"
            ]
            assert dag[0].id not in failed_events
            assert all(c.args[0] != dag[0].id for c in cascade.call_args_list)
            assert (await jobs.get(dag[1].id)).status in ("pending", "dispatched")
            # ... and the same tick still fails the job that was abandoned
            # (reap_stale will not hand this builder back on the next tick).
            abandoned = await jobs.get(x.id)
            assert abandoned.status == "failed"
            assert "went offline" in abandoned.error_message
            assert any(c.args and c.args[0] == x.id for c in cascade.call_args_list)

        run(_t())
