"""Job-state frames over the builder WebSocket are refused.

The WebSocket handlers for ``job.claim``, ``job.log``, ``job.complete`` and
``job.fail`` checked only that the token owned the *builder* it connected as,
never that it could see the *job* the frame named, and wrote no audit entry.
Any publisher could register its own builder and then, over its socket, claim
a private org's job, write that job's log and fail it -- cascade-cancelling
its dependents -- while the same calls over HTTP were 404.

No builder has ever sent these frames (every release claims, logs, completes
and fails over HTTP), so they are now refused outright: one path for
job-state changes, one place for visibility, the holder check, the status
guard, auditing and webhooks.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402

JOB_FRAMES = ("job.claim", "job.log", "job.complete", "job.fail")


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    tokens = {"admin": "admin", "owner": "publisher", "outsider": "publisher"}
    with server_on(request.param, tmp_path, monkeypatch, tokens) as (client, raw):
        r = client.post(
            "/v1/orgs",
            json={"slug": "sec", "display_name": "Sec", "is_private": True},
            headers=_h(raw["owner"]),
        )
        assert r.status_code in (200, 201), r.text
        yield client, raw


def _register(client, tok, name):
    r = client.post(
        "/v1/builders/register",
        json={"name": name, "platform": "linux", "arch": "x86_64"},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _private_chain(client, owner):
    r = client.post(
        "/v1/builds/dag",
        json={
            "dag_id": "sec-dag",
            "jobs": [
                {"recipe_name": "a", "platform": "linux", "arch": "x86_64", "org_slug": "sec"},
                {
                    "recipe_name": "b",
                    "platform": "linux",
                    "arch": "x86_64",
                    "org_slug": "sec",
                    "depends_on": [0],
                },
            ],
        },
        headers=_h(owner),
    )
    assert r.status_code == 200, r.text
    return [j["id"] for j in r.json()["jobs"]]


def _job(client, tok, job_id):
    r = client.get(f"/v1/builds/{job_id}", headers=_h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _audit_total(client, admin, job_id):
    r = client.get("/v1/audit", params={"target": str(job_id)}, headers=_h(admin))
    assert r.status_code == 200, r.text
    return r.json()["total"]


def _frame(msg_type, job_id):
    return {
        "job.claim": {"type": "job.claim", "job_id": job_id},
        "job.log": {"type": "job.log", "job_id": job_id, "data": "outsider was here\n"},
        "job.complete": {"type": "job.complete", "job_id": job_id, "archive_url": "/x"},
        "job.fail": {"type": "job.fail", "job_id": job_id, "error": "outsider failed it"},
    }[msg_type]


class TestOutsiderCannotTouchAPrivateJob:
    def test_http_already_hides_the_job(self, env):
        client, raw = env
        a, _b = _private_chain(client, raw["owner"])
        bid = _register(client, raw["outsider"], "outsider-b")
        out = _h(raw["outsider"])
        assert (
            client.post(f"/v1/builds/{a}/claim", json={"builder_id": bid}, headers=out).status_code
            == 404
        )
        assert (
            client.patch(f"/v1/builds/{a}/log", json={"data": "x"}, headers=out).status_code == 404
        )
        assert client.post(f"/v1/builds/{a}/fail", json={}, headers=out).status_code == 404

    def test_websocket_frames_change_nothing(self, env):
        client, raw = env
        a, b = _private_chain(client, raw["owner"])
        bid = _register(client, raw["outsider"], "outsider-b")
        with patch("cvcpkg.server.app.emit_webhook_event", new_callable=AsyncMock) as emit:
            with client.websocket_connect(
                f"/v1/builders/{bid}/ws",
                headers={"Authorization": f"Bearer {raw['outsider']}"},
            ) as ws:
                for msg_type in JOB_FRAMES:
                    ws.send_json(_frame(msg_type, a))
                    reply = ws.receive_json()
                    assert reply["type"] == "error", reply
                    assert reply["code"] == "http_only"
                    assert reply["rejected"] == msg_type
                    assert reply["job_id"] == a
                # Still connected, and still useful for what builders use it for.
                ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 0})
                assert ws.receive_json()["type"] == "heartbeat_ack"
        emit.assert_not_called()

        owner = raw["owner"]
        job = _job(client, owner, a)
        assert job["status"] == "pending"
        assert job["builder_id"] is None
        assert not job["error_message"]
        assert job["log_size_bytes"] in (None, 0)
        assert client.get(f"/v1/builds/{a}/log", headers=_h(owner)).status_code == 404
        # No cascade.
        assert _job(client, owner, b)["status"] == "pending"
        # Nothing audited against the job.
        assert _audit_total(client, raw["admin"], a) == 0

    def test_outsider_cannot_fail_a_job_the_owner_is_running(self, env):
        client, raw = env
        a, b = _private_chain(client, raw["owner"])
        owner_bid = _register(client, raw["owner"], "owner-b")
        outsider_bid = _register(client, raw["outsider"], "outsider-b")
        r = client.post(
            f"/v1/builds/{a}/claim", json={"builder_id": owner_bid}, headers=_h(raw["owner"])
        )
        assert r.status_code == 200 and r.json()["status"] == "running"
        with client.websocket_connect(
            f"/v1/builders/{outsider_bid}/ws",
            headers={"Authorization": f"Bearer {raw['outsider']}"},
        ) as ws:
            ws.send_json(_frame("job.fail", a))
            assert ws.receive_json()["type"] == "error"
            ws.send_json(_frame("job.complete", a))
            assert ws.receive_json()["type"] == "error"
        job = _job(client, raw["owner"], a)
        assert job["status"] == "running"
        assert job["builder_id"] == owner_bid
        assert _job(client, raw["owner"], b)["status"] == "pending"


class TestRefusedForEveryone:
    def test_the_job_owners_own_builder_is_refused_too(self, env):
        """Not an authorization decision: the frames are HTTP-only for all."""
        client, raw = env
        a, _b = _private_chain(client, raw["owner"])
        bid = _register(client, raw["owner"], "owner-b")
        with client.websocket_connect(
            f"/v1/builders/{bid}/ws", headers={"Authorization": f"Bearer {raw['owner']}"}
        ) as ws:
            for msg_type in JOB_FRAMES:
                ws.send_json(_frame(msg_type, a))
                reply = ws.receive_json()
                assert reply["type"] == "error"
                assert reply["rejected"] == msg_type
                assert "/v1/builds/{job_id}/" in reply["message"]
        assert _job(client, raw["owner"], a)["status"] == "pending"

    def test_each_refused_type_is_logged_once_per_connection(self, env, caplog):
        caplog.set_level(logging.WARNING, logger="cvcpkg.server")
        client, raw = env
        a, _b = _private_chain(client, raw["owner"])
        bid = _register(client, raw["owner"], "owner-b")
        with client.websocket_connect(
            f"/v1/builders/{bid}/ws", headers={"Authorization": f"Bearer {raw['owner']}"}
        ) as ws:
            for _ in range(3):
                ws.send_json(_frame("job.log", a))
                assert ws.receive_json()["rejected"] == "job.log"
        refused = [r for r in caplog.records if "sent job.log over WebSocket" in r.getMessage()]
        assert len(refused) == 1
