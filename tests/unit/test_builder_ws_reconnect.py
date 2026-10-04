"""The builder keeps trying to get its WebSocket back.

``cvcpkg builder run`` prefers the WebSocket: it is the only path that carries
``builder.update``, so fleet self-update (deploy-prod ``update-builders``)
reaches a builder only while its socket is up.  It used to try exactly once,
at startup: one failed handshake -- or one dropped connection later -- left
the builder on HTTP long-poll until someone restarted it.  Behind a proxy
that answered the upgrade with a 404, that was every production builder, and
fixing the proxy would not have brought a single one back.

It also never survived a successful handshake: the session called
``ws.settimeout(5)``, which the sync ``websockets`` client does not have, so
the AttributeError tore every fresh connection down as a "connection failed".

These tests pin the current behaviour:

- a failed handshake or a dropped socket puts the builder on long-poll, and
  the socket is retried on a capped exponential backoff until it connects;
- once connected the builder stops long-polling;
- a job seen on both transports (long-poll and socket push, in either order,
  across a switch) runs once -- the in-flight job-id dedupe;
- a job dispatched while the socket could not deliver it is still picked up.

The server is faked at the ``httpx.Client`` layer and the ``websockets`` sync
client is replaced by an in-memory module, so the suite needs neither a
server nor the websockets package.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
import types

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli._builder import _ReconnectBackoff, builder_run

BUILDER_ID = 7
TOKEN = "tok-not-for-logs-123"


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``builder_run`` installs global SIGINT/SIGTERM handlers; neutralize them
    so invoking it in-process doesn't clobber the test runner's handlers."""
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast_knobs(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MIN", "0.1")
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MAX", "0.2")


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = {} if data is None else data
        self.text = text
        self.content = b""

    def json(self):
        return self._data


class _FakeServer:
    """Just enough of cvcpkg-server, over HTTP and WebSocket, for one builder.

    Jobs live in ``self.jobs`` (id -> status).  ``next-job`` returns the
    lowest-id job still ``dispatched``; a claim takes ``claim_latency``
    seconds and is idempotent for this builder, like the real endpoint.  The
    recipe download 404s, so a job that gets past its claim fails fast.

    The socket: the first ``ws_failures`` handshakes raise (as a proxy that
    strips the upgrade makes them do); later ones connect.  ``on_connect``
    runs for each connection (0-based index) and can push messages or
    schedule a drop.
    """

    def __init__(self, *, ws_failures=0, claim_latency=0.0, on_connect=None):
        self.lock = threading.Lock()
        self.jobs: dict[int, str] = {}
        self.claim_latency = claim_latency
        self.claims: dict[int, int] = {}
        self.recipe_fetches: dict[int, int] = {}
        # (time, "long"|"catch-up") for every next-job poll.
        self.polls: list[tuple[float, str]] = []
        self.ws_failures = ws_failures
        self.ws_attempts = 0
        self.ws_connects: list[float] = []
        self.ws_closes: list[float] = []
        self.ws_sent: list[dict] = []
        self.on_connect = on_connect
        self.conn = None

    def dispatch(self, job_id: int) -> None:
        with self.lock:
            self.jobs[job_id] = "dispatched"

    def job(self, job_id: int) -> dict:
        return {
            "id": job_id,
            "recipe_name": f"pkg{job_id}",
            "platform": "linux",
            "arch": "x86_64",
            "config": "release",
            "link": "shared",
            "org_slug": "",
            "status": self.jobs[job_id],
            "builder_id": BUILDER_ID,
        }

    # -- HTTP --------------------------------------------------

    def client_cls(self):
        server = self

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def close(self):
                pass

            def post(self, url, **k):
                if url.endswith("/register"):
                    return _Resp(200, {"id": BUILDER_ID})
                if "/v1/builds/" in url and url.endswith("/claim"):
                    job_id = int(url.split("/")[-2])
                    time.sleep(server.claim_latency)
                    with server.lock:
                        server.claims[job_id] = server.claims.get(job_id, 0) + 1
                        if server.jobs[job_id] == "dispatched":
                            server.jobs[job_id] = "running"
                        return _Resp(200, server.job(job_id))
                if "/v1/builds/" in url and (url.endswith("/fail") or url.endswith("/complete")):
                    job_id = int(url.split("/")[-2])
                    with server.lock:
                        server.jobs[job_id] = "failed" if url.endswith("/fail") else "succeeded"
                        return _Resp(200, server.job(job_id))
                return _Resp(200, {})  # heartbeat

            def patch(self, url, json=None, **k):
                return _Resp(200, {})

            def get(self, url, params=None, **k):
                if url.endswith("/next-job"):
                    kind = "catch-up" if (params or {}).get("timeout") == "1" else "long"
                    with server.lock:
                        server.polls.append((time.time(), kind))
                        for job_id in sorted(server.jobs):
                            if server.jobs[job_id] == "dispatched":
                                return _Resp(200, server.job(job_id))
                    time.sleep(0.05)  # stand-in for the long-poll wait
                    return _Resp(204)
                if url.endswith("/v1/builds/next-claimable"):
                    time.sleep(0.05)
                    return _Resp(204)
                if "/v1/recipes/" in url:
                    recipe = url.split("/v1/recipes/")[1].split("/")[0]
                    job_id = int(recipe.removeprefix("pkg"))
                    with server.lock:
                        server.recipe_fetches[job_id] = server.recipe_fetches.get(job_id, 0) + 1
                    return _Resp(404, text="no such recipe")
                return _Resp(404)

            def delete(self, url, **k):
                return _Resp(200, {})

        return _FakeClient

    # -- WebSocket ---------------------------------------------

    def websockets_modules(self) -> dict[str, types.ModuleType]:
        """``websockets``, ``websockets.sync`` and ``websockets.sync.client``,
        with ``connect`` bound to this server."""
        server = self

        def connect(uri, **kwargs):
            with server.lock:
                server.ws_attempts += 1
                fail = server.ws_attempts <= server.ws_failures
            if fail:
                # The real InvalidURI / InvalidHandshake messages can quote
                # the URI, token and all.
                raise ConnectionError(f"server rejected WebSocket connection: HTTP 404 ({uri})")
            return _FakeConn(server)

        pkg = types.ModuleType("websockets")
        sync = types.ModuleType("websockets.sync")
        client = types.ModuleType("websockets.sync.client")
        client.connect = connect
        pkg.sync = sync
        sync.client = client
        return {"websockets": pkg, "websockets.sync": sync, "websockets.sync.client": client}


class _FakeConn:
    """The surface of ``websockets.sync.client.ClientConnection`` the builder
    uses -- and deliberately nothing more (no ``settimeout``)."""

    def __init__(self, server: _FakeServer):
        self.server = server
        self.inbox: list[dict] = []
        self.drop_at: float | None = None
        self.closed = False

    def __enter__(self):
        server = self.server
        with server.lock:
            index = len(server.ws_connects)
            server.ws_connects.append(time.time())
            server.conn = self
        if server.on_connect is not None:
            server.on_connect(server, self, index)
        return self

    def __exit__(self, *a):
        self.closed = True
        with self.server.lock:
            self.server.ws_closes.append(time.time())
        return False

    def push(self, msg: dict) -> None:
        with self.server.lock:
            self.inbox.append(msg)

    def send(self, data):
        with self.server.lock:
            self.server.ws_sent.append(json.loads(data))

    def recv(self, timeout=None):
        if self.drop_at is not None and time.time() >= self.drop_at:
            raise ConnectionError("no close frame received or sent")
        with self.server.lock:
            if self.inbox:
                return json.dumps(self.inbox.pop(0))
        time.sleep(0.02)
        raise TimeoutError


def _run(monkeypatch, tmp_path, server: _FakeServer, extra: list[str], *, max_jobs=1):
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    for mod_name, mod in server.websockets_modules().items():
        monkeypatch.setitem(sys.modules, mod_name, mod)
    return CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            TOKEN,
            "--name",
            "ws-probe",
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
        ]
        + extra,
    )


def _long_polls(server: _FakeServer) -> list[float]:
    return [t for t, kind in server.polls if kind == "long"]


# -- the backoff schedule ----------------------------------------------


def test_backoff_doubles_up_to_the_cap_and_resets():
    backoff = _ReconnectBackoff(5, 300, rand=lambda: 0.5)  # 0.5 = no jitter
    assert [backoff.next_delay() for _ in range(9)] == [5, 10, 20, 40, 80, 160, 300, 300, 300]
    backoff.reset()
    assert backoff.next_delay() == 5


def test_backoff_jitter_stays_within_bounds_and_under_the_cap():
    low = _ReconnectBackoff(10, 300, jitter=0.2, rand=lambda: 0.0)
    high = _ReconnectBackoff(10, 300, jitter=0.2, rand=lambda: 1.0)
    assert low.next_delay() == pytest.approx(8.0)
    assert high.next_delay() == pytest.approx(12.0)
    for _ in range(10):
        assert high.next_delay() <= 300


# -- reconnecting --------------------------------------------------------


def test_failed_handshakes_are_retried_until_the_socket_connects(monkeypatch, tmp_path):
    """Two refused handshakes (a proxy that drops the upgrade), then the
    server starts accepting: the builder long-polls in between, keeps
    retrying, connects, and from then on stops long-polling."""
    server = _FakeServer(ws_failures=2)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "3"])

    assert result.exit_code == 0, result.output
    assert server.ws_attempts == 3, result.output
    assert len(server.ws_connects) == 1, result.output
    connected_at = server.ws_connects[0]
    long_polls = _long_polls(server)
    assert long_polls and min(long_polls) < connected_at, "no long-poll while the socket was down"
    assert all(t < connected_at for t in long_polls), (
        "still long-polling after the socket connected:\n" + result.output
    )
    # The session survived its first moments: the old ws.settimeout() call
    # raised AttributeError straight after every handshake.
    assert "has no attribute" not in result.output
    assert "connection lost" not in result.output
    assert server.ws_closes and server.ws_closes[0] - connected_at > 1.0
    assert result.output.count("retrying WebSocket in") == 2


def test_a_dropped_socket_falls_back_to_long_poll_and_reconnects(monkeypatch, tmp_path):
    """A connection that drops is not the end of the WebSocket: the builder
    long-polls meanwhile and connects again."""
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MIN", "0.4")
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MAX", "0.4")

    def on_connect(server, conn, index):
        if index == 0:
            conn.drop_at = time.time() + 0.3

    server = _FakeServer(on_connect=on_connect)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "2.5"])

    assert result.exit_code == 0, result.output
    assert len(server.ws_connects) == 2, result.output
    assert "WebSocket connection lost" in result.output
    dropped_at, back_at = server.ws_closes[0], server.ws_connects[1]
    assert any(dropped_at <= t <= back_at for t in _long_polls(server)), (
        "the builder did not long-poll while its socket was down:\n" + result.output
    )
    assert all(t < back_at for t in _long_polls(server))


def test_missing_websockets_library_is_not_retried(monkeypatch, tmp_path):
    """No websockets package: say so once and stay on long-poll -- retrying
    an ImportError would only repeat the message."""
    server = _FakeServer()
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    for mod_name in ("websockets", "websockets.sync", "websockets.sync.client"):
        monkeypatch.setitem(sys.modules, mod_name, None)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            TOKEN,
            "--name",
            "ws-probe",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-auto-capabilities",
            "--no-free-disk",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
            "--max-runtime",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("websockets not installed") == 1, result.output
    assert "retrying WebSocket" not in result.output
    assert len(_long_polls(server)) > 1


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--no-websocket"], id="no-websocket"),
        pytest.param(["--exit-when-empty"], id="drain"),
        pytest.param(["--no-register"], id="unregistered"),
    ],
)
def test_no_socket_attempts_where_the_socket_is_not_wanted(monkeypatch, tmp_path, flags):
    server = _FakeServer()
    result = _run(monkeypatch, tmp_path, server, flags + ["--max-runtime", "1"])

    assert result.exit_code == 0, result.output
    assert server.ws_attempts == 0, result.output


def test_handshake_errors_do_not_print_the_token(monkeypatch, tmp_path):
    """The socket URL carries the bearer token as a query parameter."""
    server = _FakeServer(ws_failures=100)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "0.8"])

    assert result.exit_code == 0, result.output
    assert "WebSocket connection failed" in result.output
    assert TOKEN not in result.output


# -- never twice, across a switch ---------------------------------------------


def test_job_from_long_poll_is_not_rerun_when_the_socket_comes_back(monkeypatch, tmp_path):
    """Long-poll hands us a job; while its claim is still in flight the
    socket connects, and the job comes back twice more -- from the
    reconnect catch-up poll and as a socket push.  It runs once."""
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MIN", "0.3")

    def on_connect(server, conn, index):
        conn.push({"type": "job.dispatch", "job": server.job(1)})

    server = _FakeServer(ws_failures=1, claim_latency=2.0, on_connect=on_connect)
    server.dispatch(1)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "4"], max_jobs=2)

    assert result.exit_code == 0, result.output
    assert len(server.ws_connects) == 1, result.output
    assert server.claims == {1: 1}, result.output
    assert server.recipe_fetches == {1: 1}, result.output
    assert "already running here" in result.output


def test_job_pushed_on_the_socket_is_not_rerun_by_long_poll(monkeypatch, tmp_path):
    """The reverse switch: a job pushed on the socket, the socket drops before
    the claim lands, and long-poll hands the same job back.  It runs once."""
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MIN", "30")
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MAX", "30")

    def on_connect(server, conn, index):
        def later():
            time.sleep(0.2)  # after the connect-time catch-up poll
            server.dispatch(1)
            conn.push({"type": "job.dispatch", "job": server.job(1)})
            conn.drop_at = time.time() + 0.2

        threading.Thread(target=later, daemon=True).start()

    server = _FakeServer(claim_latency=1.5, on_connect=on_connect)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "3.5"], max_jobs=2)

    assert result.exit_code == 0, result.output
    assert "WebSocket connection lost" in result.output
    assert any(t > server.ws_closes[0] for t in _long_polls(server)), result.output
    assert server.claims == {1: 1}, result.output
    assert server.recipe_fetches == {1: 1}, result.output


# -- nothing stranded ----------------------------------------------------------


def test_job_dispatched_while_the_socket_was_down_is_picked_up(monkeypatch, tmp_path):
    """A job dispatched before the socket was registered is never pushed.
    The catch-up poll on connect finds it."""
    server = _FakeServer()
    server.dispatch(1)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "1.5"])

    assert result.exit_code == 0, result.output
    assert server.ws_attempts == 1
    assert not _long_polls(server), "connected first try, so it should never long-poll"
    assert server.claims == {1: 1}, result.output
    assert "picked up a dispatch the socket did not deliver" in result.output


def test_push_dropped_while_full_is_picked_up_when_a_slot_frees(monkeypatch, tmp_path):
    """max-jobs 1: job 2 is pushed while job 1 holds the only slot, and the
    push handler drops it.  It stays dispatched to us; the periodic catch-up
    starts it once job 1 is done."""
    monkeypatch.setenv("CVCPKG_BUILDER_WS_SWEEP_INTERVAL", "0.2")

    def on_connect(server, conn, index):
        def later():
            time.sleep(0.1)  # after the connect-time catch-up poll
            server.dispatch(1)
            server.dispatch(2)
            conn.push({"type": "job.dispatch", "job": server.job(1)})
            conn.push({"type": "job.dispatch", "job": server.job(2)})

        threading.Thread(target=later, daemon=True).start()

    server = _FakeServer(claim_latency=0.5, on_connect=on_connect)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "3"])

    assert result.exit_code == 0, result.output
    assert server.claims == {1: 1, 2: 1}, result.output
    assert server.recipe_fetches == {1: 1, 2: 1}, result.output


def test_push_dropped_while_full_starts_as_soon_as_the_slot_frees(monkeypatch, tmp_path):
    """Same drop, at the DEFAULT sweep interval (60 s): the server frees our
    slot on complete/fail while the job thread still holds it through cleanup,
    so the next dispatch -- often the dependent that completion unblocked --
    lands in that window.  It must start once the slot frees, not up to a
    minute later on the periodic sweep."""
    monkeypatch.delenv("CVCPKG_BUILDER_WS_SWEEP_INTERVAL", raising=False)

    def on_connect(server, conn, index):
        def later():
            time.sleep(0.1)  # after the connect-time catch-up poll
            server.dispatch(1)
            server.dispatch(2)
            conn.push({"type": "job.dispatch", "job": server.job(1)})
            conn.push({"type": "job.dispatch", "job": server.job(2)})

        threading.Thread(target=later, daemon=True).start()

    server = _FakeServer(claim_latency=0.5, on_connect=on_connect)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "4"])

    assert result.exit_code == 0, result.output
    assert server.claims == {1: 1, 2: 1}, result.output


def _record_self_update(monkeypatch, server, cmds: list | None = None):
    """Stub the self-update side effects; record the job states when it runs.

    Recorded at its ``git pull`` -- the first thing _self_update() does on
    every platform (POSIX then os.execv()s; Windows without the supervisor
    returns) -- so the assertion means the same thing on every OS.  Every
    command it runs is appended to *cmds*, when given.
    """
    import os
    import subprocess

    updates: list[dict] = []

    def _run(cmd, *a, **k):
        if cmds is not None:
            cmds.append(list(cmd))
        if cmd and cmd[0] == "git":
            with server.lock:
                updates.append(dict(server.jobs))

    monkeypatch.delenv("CVCPKG_BUILDER_SUPERVISED", raising=False)
    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(os, "execv", lambda *a: None)
    return updates


def test_builder_update_waits_for_in_flight_jobs(monkeypatch, tmp_path):
    """builder.update arrives while job 1 is mid-flight.  _self_update()
    os.execv()s, which would kill the job thread and leave the job "running"
    on the server until the build timeout -- so it must wait until the job is
    done, and admit nothing new meanwhile."""

    def on_connect(server, conn, index):
        def later():
            time.sleep(0.1)
            server.dispatch(1)
            conn.push({"type": "job.dispatch", "job": server.job(1)})
            time.sleep(0.3)  # job 1 is claiming (claim_latency below)
            conn.push({"type": "builder.update", "version": "999.0.0"})
            server.dispatch(2)
            conn.push({"type": "job.dispatch", "job": server.job(2)})

        threading.Thread(target=later, daemon=True).start()

    server = _FakeServer(claim_latency=1.5, on_connect=on_connect)
    execs = _record_self_update(monkeypatch, server)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "4"], max_jobs=2)

    assert result.exit_code == 0, result.output
    assert execs, "the deferred update never ran:\n" + result.output
    at_update = execs[0]
    assert at_update.get(1) not in ("dispatched", "running"), result.output
    assert at_update.get(2) == "dispatched", "admitted a new job while an update was pending"
    # os.execv is stubbed, so the update returns -- as it does when there is
    # nothing to update from.  The builder then takes work again, starting the
    # push it dropped meanwhile without waiting for the 60 s periodic sweep.
    assert server.claims.get(2) == 1, result.output


def test_builder_update_is_ignored_by_the_single_file_binary(monkeypatch, tmp_path):
    def on_connect(server, conn, index):
        conn.push({"type": "builder.update", "version": "999.0.0"})

    server = _FakeServer(on_connect=on_connect)
    execs = _record_self_update(monkeypatch, server)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "1"])

    assert result.exit_code == 0, result.output
    assert not execs
    assert "ignored (single-file binary)" in result.output


def test_builder_update_from_a_server_not_ahead_is_ignored(monkeypatch, tmp_path):
    """builder.update carries the server's own version.  A builder already
    ahead of it has nothing to update to: draining it and restarting it on
    the code it already runs would only cost it its throughput."""

    def on_connect(server, conn, index):
        conn.push({"type": "builder.update", "version": "0.0.1"})
        server.dispatch(1)
        conn.push({"type": "job.dispatch", "job": server.job(1)})

    server = _FakeServer(on_connect=on_connect)
    execs = _record_self_update(monkeypatch, server)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "1"])

    assert result.exit_code == 0, result.output
    assert not execs, result.output
    assert "update ignored" in result.output
    assert server.claims == {1: 1}, result.output


def test_self_update_never_installs_an_older_checkout(monkeypatch, tmp_path):
    """The source-checkout fallbacks can be a stale clone whose upstream no
    longer moves; installing it would downgrade the builder."""
    import cvcpkg.cli._builder as builder_mod

    def on_connect(server, conn, index):
        conn.push({"type": "builder.update", "version": "999.0.0"})

    server = _FakeServer(on_connect=on_connect)
    cmds: list[list[str]] = []
    execs = _record_self_update(monkeypatch, server, cmds)
    monkeypatch.setattr(builder_mod, "_checkout_version", lambda path: "0.0.1")
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "1"])

    assert result.exit_code == 0, result.output
    assert execs, result.output  # it did look (git pull) ...
    assert not [c for c in cmds if "pip" in c], cmds  # ... and installed nothing
    assert "not installing it" in result.output


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("2.4.0", "2.3.2", True),
        ("2.4.0", "2.4.0", False),
        ("2.3.2", "2.4.0", False),
        ("2.10.0", "2.9.9", True),  # numerically, not as strings
        ("2.4.0", "2.4.0-rc.1", True),
        ("weird", "2.4.0", True),  # unparseable: plain inequality
        ("weird", "weird", False),
    ],
)
def test_is_newer_version(candidate, current, newer):
    from cvcpkg.cli._builder import _is_newer_version

    assert _is_newer_version(candidate, current) is newer


def test_checkout_version_reads_pyproject(tmp_path):
    from cvcpkg.cli._builder import _checkout_version

    assert _checkout_version(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "cvcpkg"\nversion = "2.4.0"\n\n'
        '[tool.poetry.dependencies]\nclick = { version = "^8.1" }\n'
    )
    assert _checkout_version(tmp_path) == "2.4.0"


def test_catch_up_poll_with_a_non_json_body_keeps_the_socket(monkeypatch, tmp_path):
    """A 200 that is not JSON (a proxy error page) on the catch-up poll must
    not tear the WebSocket session down."""
    server = _FakeServer()
    cls = server.client_cls()

    class _Bad(_Resp):
        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    orig_get = cls.get

    def get(self, url, params=None, **k):
        if url.endswith("/next-job") and (params or {}).get("timeout") == "1":
            return _Bad(200)
        return orig_get(self, url, params=params, **k)

    cls.get = get
    monkeypatch.setattr(httpx, "Client", cls)
    for mod_name, mod in server.websockets_modules().items():
        monkeypatch.setitem(sys.modules, mod_name, mod)
    monkeypatch.setenv("CVCPKG_BUILDER_WS_SWEEP_INTERVAL", "0.2")
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            TOKEN,
            "--name",
            "ws-probe",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-auto-capabilities",
            "--no-free-disk",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
            "--max-runtime",
            "1.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(server.ws_connects) == 1, result.output
    assert "connection lost" not in result.output


def test_zero_retry_delay_still_long_polls_between_attempts(monkeypatch, tmp_path):
    """CVCPKG_BUILDER_WS_RETRY_MIN=0 against a server that refuses every
    handshake: the builder must still take work over long-poll between
    attempts, not retry the handshake in a hot loop."""
    monkeypatch.setenv("CVCPKG_BUILDER_WS_RETRY_MIN", "0")
    server = _FakeServer(ws_failures=10**9)
    server.dispatch(1)
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "1.5"])

    assert result.exit_code == 0, result.output[-2000:]
    assert server.claims == {1: 1}, f"ws attempts={server.ws_attempts}"


def test_token_travels_in_the_handshake_header_not_the_url(monkeypatch, tmp_path):
    """A token in the socket URL is written to every proxy access log (and the
    builder retries the socket every few minutes while it is down)."""
    seen: list[tuple[str, dict]] = []
    server = _FakeServer()
    mods = server.websockets_modules()
    real_connect = mods["websockets.sync.client"].connect

    def connect(uri, **kwargs):
        seen.append((uri, dict(kwargs.get("additional_headers") or {})))
        return real_connect(uri, **kwargs)

    mods["websockets.sync.client"].connect = connect
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    for mod_name, mod in mods.items():
        monkeypatch.setitem(sys.modules, mod_name, mod)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            TOKEN,
            "--name",
            "ws-probe",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--no-auto-capabilities",
            "--no-free-disk",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
            "--max-runtime",
            "0.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen, result.output
    uri, hdrs = seen[0]
    assert TOKEN not in uri
    assert hdrs.get("Authorization") == f"Bearer {TOKEN}"
