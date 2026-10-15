"""A builder whose registration is gone is told so over its socket.

A WebSocket opened before the builder's row was deleted (an admin unregister,
a database reset) kept answering ``heartbeat`` with ``heartbeat_ack`` while
recording nothing -- the builder believed it was reporting in and the server
showed it offline.  The socket is now closed (4004) on a heartbeat for a
builder that no longer exists, which drops the builder onto its HTTP
heartbeat, whose 404 makes it register again.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from starlette.websockets import WebSocketDisconnect  # noqa: E402

from tests.unit._build_job_backends import BACKENDS, server_on  # noqa: E402


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, monkeypatch):
    tokens = {"admin": "admin", "owner": "publisher"}
    with server_on(request.param, tmp_path, monkeypatch, tokens) as (client, raw):
        yield client, raw


def test_heartbeat_for_a_deleted_builder_closes_the_socket(env):
    client, raw = env
    r = client.post(
        "/v1/builders/register",
        json={"name": "gone-b", "platform": "linux", "arch": "x86_64"},
        headers=_h(raw["owner"]),
    )
    assert r.status_code == 200, r.text
    bid = r.json()["id"]

    with client.websocket_connect(f"/v1/builders/{bid}/ws", headers=_h(raw["owner"])) as ws:
        ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 0})
        assert ws.receive_json()["type"] == "heartbeat_ack"

        assert client.delete(f"/v1/builders/{bid}", headers=_h(raw["admin"])).status_code == 200

        ws.send_json({"type": "heartbeat", "status": "online", "current_jobs": 0})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4004
