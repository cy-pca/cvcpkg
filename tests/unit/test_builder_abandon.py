"""A builder stops a job the server no longer has running on it.

The server can be done with a job while its builder is still building it:

* a force-cancel pushes ``job.cancel`` and the build timeout ``job.timeout``,
  which the builder only logged (``job.cancel`` it did not even read);
* the offline reaper fails every job of a builder whose heartbeats lapsed
  for more than 180 s, and pushed nothing at all.

The build ran on regardless: its completion was refused ("the server did not
record it"), and meanwhile it held the builder's slot, cores and memory while
the scheduler, seeing nothing running there, dispatched it more jobs that it
never started.  On sandipaws (16 GB, two 20-core jobs) that kept its
heartbeats stalling, so each new job failed the same way (2026-10-25/26).

These tests pin the fix: on ``job.cancel``/``job.timeout``, and whenever a
heartbeat's answer -- or, from a server too old to say, a lookup of each job
-- shows a job is no longer dispatched or running on this builder, the build's
whole process tree is killed, its job root removed, its slot freed at once,
and nothing is reported.  No answer, or an unclear one, never stops a build.

The server is faked at the ``httpx.Client`` layer and the WebSocket client is
an in-memory module; the build is a real process tree (a script that starts a
child) started the way ``run_build`` starts one, through cvcpkg.proctree.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import textwrap
import threading
import time
import types
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg import proctree
from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run

BUILDER_ID = 7
JOB = 42

# A build script that starts a child, which records its pid and sleeps; the
# script waits for it.  Exits 0 after *secs* unless killed.
_CHILD = (
    "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); "
    "time.sleep(float(sys.argv[2]))"
)
_SCRIPT = """
import subprocess, sys
sys.exit(subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]]).wait())
"""


_handlers: dict = {}

# The socket tests end the run themselves once their scenario is done
# (_driven); this is only the safety net.  A fixed deadline was too short on a
# loaded machine: the socket loop stops reading at the deadline.
_SOCKET_RUN = ["--max-runtime", "300"]


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``builder_run`` installs SIGINT/SIGTERM handlers.  Record them instead of
    installing them, so a test can stop the builder as a SIGTERM would."""
    _handlers.clear()
    monkeypatch.setattr(signal, "signal", lambda sig, handler: _handlers.__setitem__(sig, handler))


def _driven(scenario) -> threading.Thread:
    """Run *scenario* on a thread, then stop the builder whatever happened.

    The builder then drains and exits, like ``kill <pid>``.
    """

    def run():
        try:
            scenario()
        finally:
            _handlers[signal.SIGTERM](signal.SIGTERM, None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")
    monkeypatch.setenv("CVCPKG_BUILDER_HEARTBEAT_INTERVAL", "0.3")
    monkeypatch.setenv("CVCPKG_BUILDER_WS_SWEEP_INTERVAL", "0.5")
    monkeypatch.setattr(_builder, "_REPORT_RETRY_DELAY", 0.0)
    monkeypatch.setattr(proctree, "KILL_GRACE_SECS", 1.0)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for(predicate, timeout: float = 120.0) -> bool:
    """Poll *predicate* until it holds.  The timeout is generous: it costs
    nothing when things go right, and on a loaded runner starting a Python
    process alone has taken over 20 s."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def _recipe_bundle(name: str) -> bytes:
    recipe_yaml = textwrap.dedent(
        f"""\
        recipe:
          name: {name}
          upstream_version: "1.0.0"
          cvc_revision: 1
        source:
          type: none
        """
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="recipe.yaml")
        info.size = len(recipe_yaml)
        tar.addfile(info, io.BytesIO(recipe_yaml))
    return buf.getvalue()


class _Resp:
    def __init__(self, status: int = 200, data=None, content: bytes = b"", text=None):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = content
        self.text = json.dumps(self._data) if text is None else text

    def json(self):
        if isinstance(self._data, BaseException):
            raise self._data
        return self._data


class _Server:
    """Enough of cvcpkg-server, over HTTP and (optionally) the socket.

    Jobs live in ``self.jobs`` (id -> status) and are handed out by next-job
    while ``dispatched``.  ``beat(server, payload)`` answers an HTTP heartbeat
    (a _Resp, or an exception to raise); ``lookup(server, job_id)`` answers
    ``GET /v1/builds/{id}`` likewise; ``ack(server, frame)`` is the extra
    fields of a socket ``heartbeat_ack`` (None: an older server's bare ack).
    """

    def __init__(self, *, beat=None, lookup=None, ack=None):
        self.lock = threading.Lock()
        self.jobs: dict[int, str] = {}
        self.posts: list[tuple[str, dict]] = []
        self.lookups: list[tuple[float, int]] = []
        self.beats: list[tuple[float, dict]] = []  # HTTP and socket heartbeats
        self.beat = beat
        self.lookup = lookup
        self.ack = ack
        self.conn = None
        self.bundle = _recipe_bundle("probe")

    def dispatch(self, job_id: int) -> None:
        with self.lock:
            self.jobs[job_id] = "dispatched"

    def job(self, job_id: int) -> dict:
        return {
            "id": job_id,
            "recipe_name": "probe",
            "platform": "linux",
            "arch": "x86_64",
            "status": self.jobs[job_id],
            "builder_id": BUILDER_ID,
        }

    def sent(self, verb: str) -> list[int]:
        return [int(u.split("/")[-2]) for u, _ in self.posts if u.endswith(f"/{verb}")]

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
                with server.lock:
                    server.posts.append((url, json))
                if url.endswith("/register"):
                    return _Resp(200, {"id": BUILDER_ID})
                if url.endswith("/heartbeat"):
                    with server.lock:
                        server.beats.append((time.monotonic(), dict(json or {})))
                    answer = server.beat(server, json) if server.beat else _Resp(200, {})
                    if isinstance(answer, BaseException):
                        raise answer
                    return answer
                job_id = int(url.split("/")[-2])
                with server.lock:
                    if url.endswith("/claim"):
                        if server.jobs.get(job_id) == "dispatched":
                            server.jobs[job_id] = "running"
                        return _Resp(200, server.job(job_id))
                    server.jobs[job_id] = "succeeded" if url.endswith("/complete") else "failed"
                    return _Resp(200, server.job(job_id))

            def patch(self, url, **k):
                return _Resp(200, {})

            def get(self, url, params=None, **k):
                if "/v1/recipes/" in url:
                    return _Resp(200, content=server.bundle)
                if url.endswith(("/next-job", "/next-claimable")):
                    with server.lock:
                        for job_id in sorted(server.jobs):
                            if server.jobs[job_id] == "dispatched":
                                return _Resp(200, server.job(job_id))
                    time.sleep(0.05)
                    return _Resp(204)
                if "/v1/builds/" in url:
                    job_id = int(url.rstrip("/").split("/")[-1])
                    with server.lock:
                        server.lookups.append((time.monotonic(), job_id))
                    answer = (
                        server.lookup(server, job_id)
                        if server.lookup
                        else _Resp(200, server.job(job_id))
                    )
                    if isinstance(answer, BaseException):
                        raise answer
                    return answer
                return _Resp(404)

            def delete(self, url, **k):
                return _Resp(200, {})

        return _Client

    def websockets_modules(self) -> dict[str, types.ModuleType]:
        server = self

        def connect(uri, **kwargs):
            return _Conn(server)

        pkg = types.ModuleType("websockets")
        sync = types.ModuleType("websockets.sync")
        client = types.ModuleType("websockets.sync.client")
        client.connect = connect
        pkg.sync = sync
        sync.client = client
        return {"websockets": pkg, "websockets.sync": sync, "websockets.sync.client": client}


class _Conn:
    def __init__(self, server: _Server):
        self.server = server
        self.inbox: list[dict] = []

    def __enter__(self):
        with self.server.lock:
            self.server.conn = self
        return self

    def __exit__(self, *a):
        return False

    def push(self, msg: dict) -> None:
        with self.server.lock:
            self.inbox.append(msg)

    def send(self, data):
        frame = json.loads(data)
        if frame.get("type") == "heartbeat":
            with self.server.lock:
                self.server.beats.append((time.monotonic(), frame))
            extra = self.server.ack(self.server, frame) if self.server.ack else None
            self.push({"type": "heartbeat_ack", **(extra or {})})

    def recv(self, timeout=None):
        with self.server.lock:
            if self.inbox:
                return json.dumps(self.inbox.pop(0))
        time.sleep(0.02)
        raise TimeoutError


class _Builds:
    """The fake ``pack_recipe``: one real process tree per job, recorded."""

    def __init__(self, tmp_path: Path, secs: dict[int, float]):
        self.tmp_path = tmp_path
        self.secs = secs  # job id -> how long its build runs if left alone
        self.started: dict[int, threading.Event] = {}
        self.pids: dict[int, int] = {}
        self.job_roots: dict[int, Path] = {}
        self.lock = threading.Lock()
        self._next = iter(sorted(secs))

    def pack(self, recipe_dir, *, output_dir, work_dir_root, **k):
        with self.lock:
            job_id = next(self._next)
            event = self.started.setdefault(job_id, threading.Event())
            self.job_roots[job_id] = Path(work_dir_root)
        pidfile = self.tmp_path / f"build-{job_id}.pid"
        proc = proctree.popen(
            [sys.executable, "-c", _SCRIPT, _CHILD, str(pidfile), str(self.secs[job_id])],
            stdin=subprocess.DEVNULL,
        )
        assert _wait_for(lambda: pidfile.is_file() and pidfile.read_text().strip())
        with self.lock:
            self.pids[job_id] = int(pidfile.read_text())
        event.set()
        if proc.wait() != 0:
            raise RuntimeError(f"build script exited with code {proc.returncode}")
        out = Path(output_dir) / "probe-1.0.0-linux-x86_64.tar.gz"
        out.write_bytes(b"archive")
        return out, "0" * 64, out.stat().st_size

    def building(self, job_id: int) -> bool:
        """Has *job_id*'s build started?  (The server's answers below wait for it,
        so a test sees a running build stopped, not one stopped before it began.)"""
        with self.lock:
            event = self.started.get(job_id)
        return event is not None and event.is_set()

    def wait_started(self, job_id: int, timeout: float = 120.0) -> bool:
        with self.lock:
            event = self.started.setdefault(job_id, threading.Event())
        return event.wait(timeout)


def _run(monkeypatch, tmp_path, server: _Server, builds: _Builds, extra: list[str], max_jobs=2):
    """Run the builder until *extra* ends it.  The default spare slot keeps the
    long-poll loop beating every 0.3 s: with every slot full it sleeps 5 s
    between beats."""
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    for mod_name, mod in server.websockets_modules().items():
        monkeypatch.setitem(sys.modules, mod_name, mod)
    monkeypatch.setattr("cvcpkg.builder.pack_recipe", builds.pack)
    monkeypatch.setattr(_builder, "_publish_to_server", lambda **k: None)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            "t",
            "--name",
            "abandon-probe",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--max-jobs",
            str(max_jobs),
            "--no-auto-capabilities",
            "--no-free-disk",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    return result.output


def _assert_stopped(server: _Server, builds: _Builds, out: str, job_id: int = JOB) -> None:
    """*job_id*'s build is dead, its job root gone, and nothing was reported."""
    assert job_id in builds.pids, out
    assert _wait_for(lambda: not _alive(builds.pids[job_id])), "the build outlived the stop"
    assert job_id not in server.sent("complete"), out
    assert job_id not in server.sent("fail"), out
    assert f"[{job_id}] Stopping the build" in out, out
    assert f"[{job_id}] Stopped: probe" in out, out
    root = builds.job_roots[job_id]
    assert _wait_for(lambda: not root.exists()), f"job root {root} left behind"


# ── Pushed over the socket ───────────────────────────────────────


@pytest.mark.parametrize(
    "push, why",
    [
        pytest.param(
            {"type": "job.cancel", "job_id": JOB, "message": "force-cancelled by admin"},
            "the server cancelled it (force-cancelled by admin)",
            id="job.cancel",
        ),
        pytest.param(
            {"type": "job.timeout", "job_id": JOB, "message": "exceeded 7200s limit"},
            "the server timed it out (exceeded 7200s limit)",
            id="job.timeout",
        ),
    ],
)
def test_a_pushed_cancel_or_timeout_stops_the_build_and_frees_the_slot(
    monkeypatch, tmp_path, push, why
):
    """The push stops the build; the next heartbeat has current_jobs 0, and
    the freed slot (max-jobs 1) takes the next job."""
    # The fake keeps the job "running": only the push may stop it here (a
    # heartbeat's lookup of a cancelled job would stop it too).
    server = _Server()
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 60.0, JOB + 1: 0.2})
    seen = {}

    def drive():
        assert builds.wait_started(JOB)
        seen["named"] = _wait_for(
            lambda: any(f["job_ids"] == [JOB] and f["current_jobs"] == 1 for _, f in server.beats)
        )
        server.conn.push(push)
        assert _wait_for(lambda: not _alive(builds.pids[JOB]))
        killed_at = time.monotonic()
        seen["dropped"] = _wait_for(
            lambda: any(t > killed_at and f["current_jobs"] == 0 for t, f in server.beats)
        )
        server.dispatch(JOB + 1)
        server.conn.push({"type": "job.dispatch", "job": server.job(JOB + 1)})
        _wait_for(lambda: JOB + 1 in server.sent("complete"))

    driver = _driven(drive)
    out = _run(monkeypatch, tmp_path, server, builds, _SOCKET_RUN, max_jobs=1)
    driver.join(timeout=30)

    _assert_stopped(server, builds, out)
    assert why in out
    assert seen.get("named"), "no heartbeat named the job while it built"
    assert seen.get("dropped"), "no heartbeat with current_jobs 0 after the stop"
    assert server.sent("complete") == [JOB + 1], out


def test_a_cancel_for_a_job_not_building_here_changes_nothing(monkeypatch, tmp_path):
    server = _Server()
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 1.5})

    def drive():
        assert builds.wait_started(JOB)
        server.conn.push({"type": "job.cancel", "job_id": 999})
        _wait_for(lambda: JOB in server.sent("complete"))

    _driven(drive)
    out = _run(monkeypatch, tmp_path, server, builds, _SOCKET_RUN)

    assert "[999] Server cancelled job, which is not building here" in out
    assert server.sent("complete") == [JOB], out


def test_heartbeat_ack_naming_the_job_stale_stops_it(monkeypatch, tmp_path):
    """A server that knows ``job_ids`` answers the socket heartbeat with
    ``stale_jobs``: no lookup needed."""

    def ack(server, frame):
        if JOB in frame.get("job_ids", []) and server.jobs.get(JOB) == "failed":
            return {"stale_jobs": [{"id": JOB, "status": "failed"}]}
        return {"stale_jobs": []}

    server = _Server(ack=ack)
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 60.0})

    def drive():
        assert builds.wait_started(JOB)
        with server.lock:
            server.jobs[JOB] = "failed"  # the offline reaper, which pushes nothing here
        _wait_for(lambda: not _alive(builds.pids[JOB]))

    _driven(drive)
    out = _run(monkeypatch, tmp_path, server, builds, _SOCKET_RUN)

    _assert_stopped(server, builds, out)
    assert "the server has it failed" in out
    assert server.lookups == [], "stale_jobs answered it; no lookup was needed"


def test_an_older_servers_bare_ack_falls_back_to_looking_each_job_up(monkeypatch, tmp_path):
    server = _Server(lookup=lambda s, j: _Resp(200, s.job(j)))
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 60.0})

    def drive():
        assert builds.wait_started(JOB)
        with server.lock:
            server.jobs[JOB] = "timed_out"
        _wait_for(lambda: not _alive(builds.pids[JOB]))

    _driven(drive)
    out = _run(monkeypatch, tmp_path, server, builds, _SOCKET_RUN)

    _assert_stopped(server, builds, out)
    assert "the server has it timed_out" in out
    assert server.lookups, "no lookup after a bare heartbeat_ack"


# ── Reconcile after a gap (HTTP heartbeats, as on long-poll) ─────


def test_after_a_heartbeat_gap_a_job_the_server_failed_is_stopped(monkeypatch, tmp_path):
    """The heartbeats fail for a while (a DNS outage, a stalled loop); the
    server's offline reaper fails the job meanwhile and pushes nothing.  The
    first beat that gets through brings it out: an older server's answer has
    no stale_jobs, so the job is looked up, and it is failed."""
    gap = {"until": None}
    err = "builder #7 (abandon-probe) went offline (no heartbeat for >180s) while job was running"

    def beat(server, payload):
        if gap["until"] is None and JOB in payload.get("job_ids", []) and builds.building(JOB):
            gap["until"] = time.monotonic() + 1.5
        if gap["until"] is not None and time.monotonic() < gap["until"]:
            with server.lock:
                server.jobs[JOB] = "failed"
            return httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        return _Resp(200, {"id": BUILDER_ID})  # an older server: no stale_jobs

    def lookup(server, job_id):
        return _Resp(200, {**server.job(job_id), "error_message": err})

    server = _Server(beat=beat, lookup=lookup)
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 60.0})
    # One slot: the loop then beats once more after the stop, before it finds
    # the queue empty and exits, and that beat shows the slot free.
    out = _run(
        monkeypatch, tmp_path, server, builds, ["--no-websocket", "--exit-when-empty"], max_jobs=1
    )

    _assert_stopped(server, builds, out)
    assert "heartbeat error: [Errno 11001] getaddrinfo failed" in out
    assert f"the server has it failed: {err}" in out
    # Lookups before the gap found it running here and kept it; the stop
    # came from the first one after.
    assert any(t >= gap["until"] for t, _ in server.lookups), server.lookups
    later = [p for t, p in server.beats if t >= gap["until"]]
    assert any(p["current_jobs"] == 0 for p in later), "current_jobs never dropped"


def test_heartbeat_answer_with_stale_jobs_stops_the_job_without_a_lookup(monkeypatch, tmp_path):
    def beat(server, payload):
        gone = JOB in payload["job_ids"] and builds.building(JOB)
        stale = [{"id": JOB, "status": "cancelled"}] if gone else []
        return _Resp(200, {"id": BUILDER_ID, "stale_jobs": stale})

    server = _Server(beat=beat)
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 60.0})
    out = _run(monkeypatch, tmp_path, server, builds, ["--no-websocket", "--exit-when-empty"])

    _assert_stopped(server, builds, out)
    assert "the server has it cancelled" in out
    assert server.lookups == []


def test_a_job_moved_to_another_builder_is_stopped(monkeypatch, tmp_path):
    def lookup(server, job_id):
        holder = 8 if builds.building(job_id) else BUILDER_ID
        return _Resp(200, {**server.job(job_id), "status": "running", "builder_id": holder})

    server = _Server(lookup=lookup)
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 60.0})
    out = _run(monkeypatch, tmp_path, server, builds, ["--no-websocket", "--exit-when-empty"])

    _assert_stopped(server, builds, out)
    assert "the server has it running on builder #8" in out


# ── Never on a guess ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "lookup",
    [
        pytest.param(lambda s, j: httpx.ConnectError("getaddrinfo failed"), id="unreachable"),
        pytest.param(lambda s, j: httpx.ReadTimeout("slow"), id="timeout"),
        pytest.param(lambda s, j: _Resp(503, text="Service Unavailable"), id="503"),
        pytest.param(lambda s, j: _Resp(404, {"detail": "not found"}), id="404"),
        pytest.param(lambda s, j: _Resp(200, ValueError("not JSON"), text="<html>"), id="html"),
        pytest.param(lambda s, j: _Resp(200, {"id": j}), id="no-status"),
        pytest.param(lambda s, j: _Resp(200, s.job(j)), id="still-running-here"),
    ],
)
def test_a_build_is_kept_unless_the_server_clearly_says_otherwise(monkeypatch, tmp_path, lookup):
    server = _Server(lookup=lookup)
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 1.5})
    out = _run(monkeypatch, tmp_path, server, builds, ["--no-websocket", "--exit-when-empty"])

    assert server.lookups, "the job was never checked"
    assert server.sent("complete") == [JOB], out
    assert "Stopping the build" not in out


def test_a_build_is_kept_while_heartbeats_fail(monkeypatch, tmp_path):
    server = _Server(beat=lambda s, p: httpx.ConnectError("getaddrinfo failed"))
    server.dispatch(JOB)
    builds = _Builds(tmp_path, {JOB: 1.5})
    out = _run(monkeypatch, tmp_path, server, builds, ["--no-websocket", "--exit-when-empty"])

    assert "heartbeat error" in out
    assert server.sent("complete") == [JOB], out
    assert "Stopping the build" not in out
