"""A heartbeat tells a builder which of its jobs the server is done with.

The offline reaper fails every job of a builder whose heartbeats lapsed and,
until now, told the builder nothing, so it built on for a result the server
would refuse (sandipaws, 2026-10-25/26).  Two channels now carry it:

* a heartbeat that names the jobs being built (``job_ids``) is answered with
  ``stale_jobs``: those not dispatched or running on that builder, with their
  status when they were its jobs.  A beat without ``job_ids`` (an older
  builder) is answered as before.  Over HTTP and over the socket;
* the reaper pushes ``job.cancel`` for each job it fails, for a builder whose
  socket outlived its heartbeats (a stalled loop).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    with server_on(request.param, tmp_path, monkeypatch, {"owner": "publisher"}) as (client, raw):
        yield client, raw["owner"]


def _register(client, tok, name):
    r = client.post(
        "/v1/builders/register",
        json={"name": name, "platform": "linux", "arch": "x86_64", "max_jobs": 2},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _running_job(client, tok, builder_id, recipe):
    r = client.post(
        "/v1/builds/dag",
        json={
            "dag_id": f"dag-{recipe}",
            "jobs": [{"recipe_name": recipe, "platform": "linux", "arch": "x86_64"}],
        },
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["jobs"][0]["id"]
    r = client.post(f"/v1/builds/{job_id}/claim", json={"builder_id": builder_id}, headers=_h(tok))
    assert r.status_code == 200 and r.json()["status"] == "running", r.text
    return job_id


def _beat(client, tok, builder_id, **extra):
    r = client.post(
        f"/v1/builders/{builder_id}/heartbeat",
        json={"status": "online", "current_jobs": 1, **extra},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_a_beat_without_job_ids_is_answered_as_before(env):
    client, tok = env
    bid = _register(client, tok, "old-agent")
    _running_job(client, tok, bid, "a")

    answer = _beat(client, tok, bid)

    assert answer["id"] == bid
    assert answer.get("stale_jobs") is None


def test_stale_jobs_lists_what_the_server_no_longer_has_on_the_builder(env):
    client, tok = env
    bid = _register(client, tok, "b1")
    other = _register(client, tok, "b2")
    mine = _running_job(client, tok, bid, "mine")
    theirs = _running_job(client, tok, other, "theirs")

    assert _beat(client, tok, bid, job_ids=[mine])["stale_jobs"] == []

    # The reaper's verdict, as it records it.
    r = client.post(
        f"/v1/builds/{mine}/fail",
        json={"error_message": "builder went offline", "builder_id": bid},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text

    stale = _beat(client, tok, bid, job_ids=[mine, theirs, 999999])["stale_jobs"]
    assert stale == [
        {"id": mine, "status": "failed"},
        # Not this builder's: it learns only that the job is not its own.
        {"id": theirs, "status": None},
        {"id": 999999, "status": None},
    ]
    assert _beat(client, tok, other, job_ids=[theirs])["stale_jobs"] == []


def test_the_socket_heartbeat_ack_carries_stale_jobs(env):
    client, tok = env
    bid = _register(client, tok, "ws-b")
    job = _running_job(client, tok, bid, "w")

    with client.websocket_connect(f"/v1/builders/{bid}/ws", headers=_h(tok)) as ws:
        ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 1})
        assert ws.receive_json() == {"type": "heartbeat_ack"}, "an older builder's ack changed"

        ws.send_json({"type": "heartbeat", "current_jobs": 1, "job_ids": [job]})
        assert ws.receive_json() == {"type": "heartbeat_ack", "stale_jobs": []}

        r = client.post(f"/v1/builds/{job}/cancel", params={"force": "true"}, headers=_h(tok))
        assert r.status_code == 200, r.text
        assert ws.receive_json()["type"] == "job.cancel"  # the force-cancel's own push

        ws.send_json({"type": "heartbeat", "current_jobs": 1, "job_ids": [job, "x", True]})
        ack = ws.receive_json()
        assert ack == {"type": "heartbeat_ack", "stale_jobs": [{"id": job, "status": "cancelled"}]}


# ── The offline reaper tells the builder ───────────────────────────


def test_the_offline_reaper_pushes_job_cancel_for_each_job_it_fails(tmp_path, monkeypatch):
    import cvcpkg.server.app as app_mod
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbBuilderStore, DbBuildJobStore

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'reap.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

    async def _t():
        init_db(db_url)
        await create_tables()
        try:
            builders, jobs = DbBuilderStore(), DbBuildJobStore()
            b = await builders.register("bx", "linux", "x86_64", "root")
            x = await jobs.create("x", "linux", "x86_64", "ci")
            await jobs.claim(x.id, b.id)

            async def reap_stale(max_age_seconds=180):
                return [await builders.get(b.id)]

            monkeypatch.setattr(builders, "reap_stale", reap_stale)
            monkeypatch.setattr(app_mod, "_REAPER_STARTUP_GRACE", 0)
            monkeypatch.setattr(app_mod, "_use_db", True)
            monkeypatch.setattr(app_mod, "_db_builders", builders)
            monkeypatch.setattr(app_mod, "_db_build_jobs", jobs)
            monkeypatch.setattr(app_mod, "emit_webhook_event", AsyncMock())
            push = AsyncMock(return_value=True)
            monkeypatch.setattr(app_mod, "_ws_send", push)

            ticks = {"n": 0}

            async def one_tick(_secs):
                ticks["n"] += 1
                if ticks["n"] > 1:
                    raise asyncio.CancelledError

            monkeypatch.setattr(asyncio, "sleep", one_tick)
            with pytest.raises(asyncio.CancelledError):
                await app_mod._build_scheduler_loop()

            assert (await jobs.get(x.id)).status == "failed"
            cancels = [c.args for c in push.await_args_list if c.args[1]["type"] == "job.cancel"]
            assert len(cancels) == 1, push.await_args_list
            to, msg = cancels[0]
            assert to == b.id and msg["job_id"] == x.id
            assert "went offline" in msg["message"] and msg["reason"] == "builder_reaped"
        finally:
            await dispose_engine()

    asyncio.run(_t())
