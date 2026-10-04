"""The builder WebSocket must work behind a real uvicorn, not only TestClient.

Starlette's ``TestClient`` drives the ASGI app directly, so every WebSocket
test in this suite passed while cvcpkg.org answered the builder handshake with
a plain ``404 {"detail":"Not Found"}``.  uvicorn ships no WebSocket protocol of
its own (``--ws auto`` = ``websockets``, else ``wsproto``) and the server's
dependency closure carried neither, so uvicorn logged "Unsupported upgrade
request" and served the upgrade as ordinary HTTP -- and no HTTP route matches
``/v1/builders/{id}/ws``.  Every builder fell back to HTTP long-poll, and the
server-pushed ``builder.update`` reached nobody.

These tests put the real app behind a real uvicorn socket and send a real
RFC 6455 handshake, which is the only layer where that failure is visible.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib.util
import json
import os
import socket
import threading
import time
import urllib.request

import pytest

uvicorn = pytest.importorskip("uvicorn", reason="server extras not installed")
pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required for builder tests")

from cvcpkg.server import cli as server_cli_mod  # noqa: E402
from cvcpkg.server.models import TokenRole  # noqa: E402


def _handshake_status(port: int, path: str) -> str:
    """Send an RFC 6455 opening handshake; return the response status line."""
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request.encode())
        data = b""
        while b"\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return data.split(b"\r\n", 1)[0].decode("latin-1")


@contextlib.contextmanager
def _serve(app):
    """Run *app* on a real uvicorn socket in a thread; yield the bound port."""
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        ws="auto",  # what `cvcpkg-server run` gets: uvicorn's default
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@pytest.fixture()
def bare_server(tmp_path, monkeypatch):
    """The real app (YAML backend, nothing seeded) behind a real uvicorn."""
    monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    from cvcpkg.server.app import create_app

    with _serve(create_app(state_dir=tmp_path)) as port:
        yield port


@pytest.fixture()
def seeded_server(tmp_path, monkeypatch):
    """The real app (SQLite backend, one admin token) behind a real uvicorn."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed() -> str:
        init_db(db_url)
        await create_tables()
        admin = await DbTokenStore(tmp_path).create("admin", TokenRole.admin)
        await dispose_engine()
        return admin

    admin_token = asyncio.run(_seed())
    with _serve(create_app(state_dir=tmp_path)) as port:
        yield port, admin_token


def _register_builder(port: int, token: str) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/builders/register",
        data=json.dumps({"name": "ws-probe", "platform": "linux", "arch": "x86_64"}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return int(json.load(resp)["id"])


class TestBuilderWebSocketOverUvicorn:
    def test_rejected_handshake_reaches_the_app_not_a_404(self, bare_server):
        """A bad token must be refused BY THE WS HANDLER (403), not 404'd.

        404 is exactly what production served: uvicorn without a WebSocket
        library treats the upgrade as plain HTTP and nothing routes it.
        """
        status = _handshake_status(bare_server, "/v1/builders/1/ws?token=not-a-token")
        assert " 404 " not in status, f"WebSocket upgrade was served as HTTP: {status!r}"
        assert " 403 " in status, status

    def test_owner_handshake_switches_protocols(self, seeded_server):
        port, admin = seeded_server
        builder_id = _register_builder(port, admin)
        status = _handshake_status(port, f"/v1/builders/{builder_id}/ws?token={admin}")
        assert " 101 " in status, status

    def test_builder_client_authenticates_with_the_header(self, seeded_server):
        """The builder's own client call: websockets.sync.client with the token
        in the Authorization header and none in the URL (a query string lands
        in every proxy access log).  The handshake is accepted and the session
        carries frames both ways."""
        ws_sync = pytest.importorskip("websockets.sync.client")
        port, admin = seeded_server
        builder_id = _register_builder(port, admin)
        with ws_sync.connect(
            f"ws://127.0.0.1:{port}/v1/builders/{builder_id}/ws",
            additional_headers={"Authorization": f"Bearer {admin}"},
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            ws.send(json.dumps({"type": "heartbeat", "status": "online", "current_jobs": 0}))
            assert json.loads(ws.recv(timeout=10))["type"] == "heartbeat_ack"

    def test_handshake_without_any_token_is_refused(self, seeded_server):
        port, admin = seeded_server
        builder_id = _register_builder(port, admin)
        status = _handshake_status(port, f"/v1/builders/{builder_id}/ws")
        assert " 403 " in status, status


class TestWebSocketLibraryDetection:
    def test_the_server_closure_carries_a_websocket_library(self):
        """CI installs the [server] extra from the lock; it must bring one."""
        assert server_cli_mod._websocket_library() is not None

    @pytest.mark.parametrize(
        ("present", "expected"),
        [
            ({"websockets", "wsproto"}, "websockets"),
            ({"wsproto"}, "wsproto"),
            (set(), None),
        ],
    )
    def test_detection_follows_uvicorns_auto_order(self, monkeypatch, present, expected):
        real = importlib.util.find_spec

        def _find_spec(name, *a, **kw):
            if name in ("websockets", "wsproto"):
                return object() if name in present else None
            return real(name, *a, **kw)

        monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
        assert server_cli_mod._websocket_library() == expected

    @pytest.mark.parametrize(
        ("lib", "needle"),
        [
            (None, "no WebSocket library"),
            ("websockets", "builder WebSocket: websockets"),
        ],
    )
    def test_run_reports_websocket_support_at_startup(self, monkeypatch, tmp_path, lib, needle):
        import click
        from click.testing import CliRunner

        monkeypatch.setattr(server_cli_mod, "_websocket_library", lambda: lib)

        def _fake_run(*a, **kw):
            raise click.exceptions.Exit(0)

        monkeypatch.setattr(uvicorn, "run", _fake_run)
        result = CliRunner().invoke(
            server_cli_mod.server_cli, ["run", "--state-dir", str(tmp_path)]
        )
        assert needle in result.output, result.output
