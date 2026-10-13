# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""A deploy must not cut off the archive downloads in flight.

Every production deploy stops the server container (SIGTERM, then SIGKILL
after the stop grace period).  The app's lifespan used to install its own
SIGTERM handler that raised SystemExit, replacing uvicorn's graceful one, so
the event loop was torn down mid-response: each download in progress ended at
a 64 KiB chunk boundary with a clean EOF.  On 2026-10-12 that surfaced in CI
as "sha256 mismatch" for python311 and emsdk during a prod deploy.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

pytest.importorskip("uvicorn", reason="server extras not installed")
pytest.importorskip("fastapi", reason="server extras not installed")

_ROOT = Path(__file__).resolve().parents[2]


def test_lifespan_leaves_sigterm_to_uvicorn(tmp_path, monkeypatch):
    """uvicorn installs its handler before the lifespan runs; the app must not
    replace it (that handler is what drains in-flight responses)."""
    monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
    from cvcpkg.server.app import create_app

    app = create_app(state_dir=tmp_path)

    def uvicorns_handler(signum, frame):  # stands in for Server.handle_exit
        pass

    previous = signal.signal(signal.SIGTERM, uvicorns_handler)
    try:

        async def _cycle() -> None:
            async with app.router.lifespan_context(app):
                assert signal.getsignal(signal.SIGTERM) is uvicorns_handler

        asyncio.run(_cycle())
        assert signal.getsignal(signal.SIGTERM) is uvicorns_handler
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_compose_stop_grace_outlasts_the_server_drain():
    """Docker's default 10s stop timeout would SIGKILL the drain."""
    from cvcpkg.server.cli import run

    drain = next(p.default for p in run.params if p.name == "graceful_timeout")
    compose = yaml.safe_load((_ROOT / "docker-compose.production.yml").read_text())
    grace = compose["services"]["backend"]["stop_grace_period"]
    assert grace.endswith("s")
    assert int(grace[:-1]) > drain > 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sigterm_mid_download_lets_the_body_finish(tmp_path):
    """End to end: a real server process gets SIGTERM while a client is part
    way through an archive.  The client must receive every byte."""
    state = tmp_path / "state"
    (state / "archives").mkdir(parents=True)
    name = "big-1.0-linux-x86_64-release-shared.tar.zst"
    data = os.urandom(32 << 20)
    (state / "archives" / name).write_bytes(data)

    port = _free_port()
    env = dict(os.environ, CVCPKG_SERVER_STATE_DIR=str(state))
    env.pop("CVCPKG_DATABASE_URL", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_ROOT / "src"), *filter(None, [env.get("PYTHONPATH")])]
    )
    srv = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import uvicorn; uvicorn.run('cvcpkg.server.app:create_app', factory=True, "
            f"host='127.0.0.1', port={port}, log_level='warning', "
            "timeout_graceful_shutdown=30)",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while True:
            try:
                urllib.request.urlopen(base + "/healthz", timeout=2).read()
                break
            except OSError:
                if srv.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"server did not start: {srv.stderr.read().decode()[-2000:]}")
                time.sleep(0.2)

        h = hashlib.sha256()
        got = 0
        with urllib.request.urlopen(f"{base}/v1/download/{name}", timeout=60) as resp:
            expected = int(resp.headers["Content-Length"])
            while chunk := resp.read(1 << 16):
                got += len(chunk)
                h.update(chunk)
                if got == 1 << 20:
                    srv.send_signal(signal.SIGTERM)
                time.sleep(0.001)  # a consumer slower than loopback
        assert got == expected == len(data)
        assert h.hexdigest() == hashlib.sha256(data).hexdigest()
        srv.wait(timeout=60)
    finally:
        if srv.poll() is None:
            srv.kill()
            srv.wait()
