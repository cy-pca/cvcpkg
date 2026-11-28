"""The startup sweep runs only once the single-instance guard has passed.

``builder run`` deletes every job dir in its work dir at startup: anything
there is an orphan of a previous incarnation, *because* the pidfile guard keeps
a second builder off a live one's work dir.  The sweep used to run before that
guard, so a second ``cvcpkg builder run`` pointed at a live builder's work dir
deleted the live builder's in-flight job dirs, and only then refused to start.

Under ``--daemon`` the guard is checked, and the sweep run, in the foreground
before the fork: a refusal then fails visibly instead of in a detached child
writing to /dev/null, and the sweep's line reaches the terminal (or a
Type=forking unit's journal).  The process left after the fork checks the
pidfile again and writes it.

The server is faked at the ``httpx.Client`` layer, as in
test_builder_stranded_claims.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.builder_claims import ClaimLedger
from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run

SERVER = "http://test"
NAME = "sweep-test"


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")
    monkeypatch.setattr(_builder, "_REPORT_RETRY_DELAY", 0.0, raising=False)


@pytest.fixture
def live_pid():
    """A live python process, which the guard takes for a running builder."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    yield proc.pid
    proc.kill()
    proc.wait(timeout=30)


@pytest.fixture
def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


class _Resp:
    def __init__(self, status: int = 200, data=None):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = b""
        self.text = ""

    def json(self):
        return self._data


class _Server:
    """Registers the builder as #7 and never has a job for it."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []

    def client_cls(self):
        server = self

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
                server.requests.append(("POST", url))
                if url.endswith("/register"):
                    return _Resp(200, {"id": 7})
                return _Resp(200, {})

            def patch(self, url, **k):
                server.requests.append(("PATCH", url))
                return _Resp(200, {})

            def get(self, url, **k):
                server.requests.append(("GET", url))
                return _Resp(204)

            def delete(self, url, **k):
                server.requests.append(("DELETE", url))
                return _Resp(200, {})

        return _Client


def _work_dir(tmp_path: Path) -> Path:
    """A work dir as a running builder leaves it: two job dirs mid-build."""
    wd = tmp_path / "wd"
    for name in ("cvcpkg-job-zlib-abc123", "cvcpkg-t-def456"):
        (wd / name / "src").mkdir(parents=True)
        (wd / name / "src" / "build.log").write_text("still building\n")
    return wd


def _job_dirs(wd: Path) -> list[str]:
    return sorted(p.name for p in wd.iterdir() if p.is_dir() and p.name.startswith("cvcpkg-"))


def _invoke(monkeypatch, wd: Path, server: _Server, *extra: str):
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    # The default pidfile, <work dir>/cvcpkg-builder.pid, as deployed builders
    # use it (run-builder.sh --restart reads it there).
    return CliRunner().invoke(
        builder_run,
        [
            "--server",
            SERVER,
            "--token",
            "t",
            "--name",
            NAME,
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
            str(wd),
            "--recipe-cache-dir",
            str(wd.parent / "rc"),
            *extra,
        ],
    )


def test_a_second_builder_refuses_and_leaves_the_live_ones_job_dirs(
    monkeypatch, tmp_path, live_pid
):
    wd = _work_dir(tmp_path)
    pidfile = wd / "cvcpkg-builder.pid"
    pidfile.write_text(str(live_pid))
    ledger = ClaimLedger(wd, server=SERVER, builder=NAME)
    assert ledger.record(42, identity={"builder_id": 7}, recipe="zlib")
    server = _Server()

    result = _invoke(monkeypatch, wd, server)

    assert result.exit_code == 1, result.output
    assert "refusing to start a second instance" in result.output
    assert "reclaimed" not in result.output
    # The live builder's jobs are untouched, files and all.
    assert _job_dirs(wd) == ["cvcpkg-job-zlib-abc123", "cvcpkg-t-def456"]
    assert (wd / "cvcpkg-job-zlib-abc123" / "src" / "build.log").read_text() == "still building\n"
    # So are its pidfile and its claim, and the server never heard of us.
    assert pidfile.read_text() == str(live_pid)
    assert [r["job_id"] for r in ledger.stranded()] == [42]
    assert server.requests == []


def test_a_refused_daemon_start_fails_in_the_foreground_before_any_fork(
    monkeypatch, tmp_path, live_pid
):
    wd = _work_dir(tmp_path)
    (wd / "cvcpkg-builder.pid").write_text(str(live_pid))

    def no_fork():
        raise AssertionError("forked before the single-instance guard refused")

    monkeypatch.setattr(os, "fork", no_fork, raising=False)

    result = _invoke(monkeypatch, wd, _Server(), "--daemon")

    assert result.exit_code == 1, result.output
    assert "refusing to start a second instance" in result.output
    assert "daemonizing" not in result.output
    assert _job_dirs(wd) == ["cvcpkg-job-zlib-abc123", "cvcpkg-t-def456"]


def test_with_a_stale_pidfile_the_orphans_are_still_swept(monkeypatch, tmp_path, dead_pid):
    wd = _work_dir(tmp_path)
    (wd / "cvcpkg-builder.pid").write_text(str(dead_pid))
    # The claims ledger lives in the work dir too, and is not a job dir.
    ClaimLedger(wd, server="http://elsewhere", builder="other").record(
        1, identity={"builder_id": 2}
    )

    result = _invoke(monkeypatch, wd, _Server())

    assert result.exit_code == 0, result.output
    assert "reclaimed 2 orphaned job dir(s)" in result.output
    assert _job_dirs(wd) == []
    other = ClaimLedger(wd, server="http://elsewhere", builder="other")
    assert [r["job_id"] for r in other.stranded()] == [1]


@pytest.mark.skipif(sys.platform == "win32", reason="--daemon is not supported on Windows")
def test_under_daemon_the_sweep_line_prints_before_the_fork(monkeypatch, tmp_path, dead_pid):
    wd = _work_dir(tmp_path)
    (wd / "cvcpkg-builder.pid").write_text(str(dead_pid))
    forks: list[str] = []

    def parent_side_of_fork():
        # The foreground process: it exits 0 here, and what it printed up to
        # now is all a terminal or a Type=forking unit's journal ever sees.
        forks.append("fork")
        return 12345

    monkeypatch.setattr(os, "fork", parent_side_of_fork)

    result = _invoke(monkeypatch, wd, _Server(), "--daemon")

    assert result.exit_code == 0, result.output
    assert forks == ["fork"]
    out = result.output
    assert "reclaimed 2 orphaned job dir(s)" in out
    assert out.index("reclaimed 2 orphaned job dir(s)") < out.index("daemonizing")
    assert _job_dirs(wd) == []
    # The stale pidfile is gone; the daemon writes its own after the fork.
    assert not (wd / "cvcpkg-builder.pid").exists()
