# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""End-to-end: the real ``oauth_native`` client against the real broker server.

The client unit tests drive ``oauth_native`` against a stub that mirrors the
broker contract; this closes the loop by running the actual FastAPI app in a
threaded uvicorn and driving ``pairing_login`` over real sockets, so a mismatch
between the client's request shapes and the live endpoints fails the test.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")
uvicorn = pytest.importorskip("uvicorn", reason="uvicorn required for a live server")

import httpx

from cvcpkg import oauth_native
from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'flow.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")
    monkeypatch.setenv("CVCPKG_CLI_POLL_INTERVAL", "1")
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        admin = await DbTokenStore(tmp_path).create("admin-tok", TokenRole.admin)
        await dispose_engine()
        return admin

    admin = asyncio.run(_seed())

    port = _free_port()
    app = create_app(state_dir=tmp_path)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError("live server did not start")

    yield base, admin

    server.should_exit = True
    thread.join(timeout=10)


def _wait_for_code(captured, deadline=15.0):
    end = time.time() + deadline
    while time.time() < end:
        for line in captured:
            m = re.search(r"([2-9A-Z]{4}-[2-9A-Z]{4})", str(line))
            if m:
                return m.group(1)
        time.sleep(0.1)
    raise AssertionError("user code was never printed")


def test_pairing_flow_end_to_end(live_server, tmp_path, monkeypatch):
    base, admin = live_server
    monkeypatch.setenv("CVCPKG_CREDENTIALS_FILE", str(tmp_path / "creds.yaml"))

    captured: list = []
    result: dict = {}

    def run_login():
        try:
            result["cred"] = oauth_native.pairing_login(
                base, role="reader", timeout=60, printer=captured.append
            )
        except Exception as exc:  # noqa: BLE001
            result["err"] = exc

    t = threading.Thread(target=run_login)
    t.start()

    code = _wait_for_code(captured)

    # Approve as the human: a browser session (token login) then /link/approve.
    with httpx.Client(base_url=base, timeout=10, follow_redirects=True) as c:
        assert c.post("/login", data={"token": admin, "next": "/account"}).status_code in (200, 303)
        page = c.get(f"/link?code={code}")
        assert page.status_code == 200 and "Approve this device?" in page.text
        csrf = re.search(r'name="_csrf" value="([0-9a-f]+)"', page.text).group(1)
        approve = c.post("/link/approve", data={"_csrf": csrf, "user_code": code, "role": "reader"})
        assert approve.status_code == 200

    t.join(timeout=60)
    assert "err" not in result, result.get("err")
    cred = result["cred"]
    assert cred.access.startswith("cvcses_")
    assert cred.principal == "admin-tok"
    assert cred.role == "reader"

    # The minted session is a real bearer that resolves to the principal.
    who = httpx.get(
        f"{base}/v1/auth/whoami",
        headers={"Authorization": f"Bearer {cred.access}"},
        timeout=10,
    )
    assert who.status_code == 200
    assert who.json()["name"] == "admin-tok" and who.json()["kind"] == "session"
