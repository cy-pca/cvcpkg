"""A builder survives writing to a connection its peer has reset.

Dev job 25560 (grpc, netbsd, 2026-11-27): the server dropped the worker's
WebSocket ("keepalive ping timeout"), the worker went on writing to the server
-- the build log it streams, heartbeats, the next-job poll -- and died of
SIGPIPE mid-build (the fleet log: ``exited (code -13)``).  The cvcpkg CLI
restores SIGPIPE's default action for every command, so ``cvcpkg recipes |
head`` ends quietly; ``builder run`` must ignore it again, so that such a write
raises BrokenPipeError into the ``except`` that already keeps the builder up.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import cvcpkg

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="no SIGPIPE on Windows")

# A process set up the way the CLI sets up every command, and then -- when
# asked -- the way `builder run` sets itself up, writes to a socket whose peer
# is gone, then starts a child the way the builder starts a build script.
_CHILD = textwrap.dedent(
    """
    import signal, socket, subprocess, sys

    from cvcpkg import proctree
    from cvcpkg.cli import _restore_default_sigpipe
    from cvcpkg.cli._builder import _ignore_sigpipe

    _restore_default_sigpipe()  # the cvcpkg CLI group, for every command
    if sys.argv[1] == "builder":
        _ignore_sigpipe()  # builder run, first thing

    a, b = socket.socketpair()
    b.close()
    try:
        for _ in range(8):
            a.sendall(b"x" * 65536)
    except BrokenPipeError:
        print("raised BrokenPipeError", flush=True)

    if sys.platform.startswith("linux"):
        proc = proctree.popen(
            ["grep", "^SigIgn", "/proc/self/status"], stdout=subprocess.PIPE, text=True
        )
        mask = int(proc.communicate()[0].split()[1], 16)
        ignored = bool(mask & (1 << (signal.SIGPIPE - 1)))
        print("script ignores SIGPIPE" if ignored else "script has default SIGPIPE")
    """
)


def _run_child(mode: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    src = str(Path(cvcpkg.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", _CHILD, mode],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


@posix_only
def test_without_it_a_write_to_a_reset_connection_kills_the_process():
    """The control: what the worker on .214 died of."""
    proc = _run_child("cli")
    assert proc.returncode == -signal.SIGPIPE, (proc.returncode, proc.stdout, proc.stderr)
    assert "raised BrokenPipeError" not in proc.stdout


@posix_only
def test_a_builder_gets_broken_pipe_error_instead_and_keeps_running():
    proc = _run_child("builder")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "raised BrokenPipeError" in proc.stdout


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/self/status")
def test_the_build_scripts_a_builder_starts_keep_the_default_action():
    """subprocess puts SIGPIPE back in every child (restore_signals)."""
    proc = _run_child("builder")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "script has default SIGPIPE" in proc.stdout


@posix_only
def test_builder_run_ignores_sigpipe_before_it_talks_to_the_server(monkeypatch, tmp_path):
    """And puts the previous action back when the command ends."""
    import httpx
    from click.testing import CliRunner

    from cvcpkg.cli import _builder
    from cvcpkg.cli._builder import builder_run

    calls: list[tuple[int, object]] = []

    def fake_signal(signum, handler):
        calls.append((signum, handler))
        return "the previous action" if signum == signal.SIGPIPE else None

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")
    monkeypatch.setattr(_builder, "_REPORT_RETRY_DELAY", 0.0, raising=False)

    at_register: list[list[tuple[int, object]]] = []

    class _Resp:
        def __init__(self, status=200, data=None):
            self.status_code = status
            self._data = data or {}
            self.text = ""

        def json(self):
            return self._data

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def post(self, url, json=None, **k):
            if url.endswith("/register"):
                at_register.append(list(calls))
                return _Resp(200, {"id": 3})
            return _Resp(200)

        def patch(self, url, **k):
            return _Resp(200)

        def get(self, url, **k):
            return _Resp(204)

        def delete(self, url, **k):
            return _Resp(200)

    monkeypatch.setattr(httpx, "Client", _Client)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            "t",
            "--name",
            "w-sigpipe",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-websocket",
            "--no-auto-capabilities",
            "--no-free-disk",
            "--exit-when-empty",
            "--max-runtime",
            "60",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert at_register, "the builder never registered"
    assert (signal.SIGPIPE, signal.SIG_IGN) in at_register[0]
    sigpipe_calls = [h for s, h in calls if s == signal.SIGPIPE]
    assert sigpipe_calls[-1] == "the previous action", sigpipe_calls
