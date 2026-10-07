"""Job admission edge cases: one job id, one thread -- and never a stuck one.

Companion to test_builder_no_duplicate_jobs.py.  Covers what the in-flight
job-id mark must NOT do (outlive a job whose claim failed or whose thread never
started, or kill the poll loop on a 200 that is not a job), the WebSocket
dispatch path, and a claim whose answer is lost after it landed.
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

from cvcpkg.cli._builder import builder_run


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast_drain_settle(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = {} if data is None else data
        self.text = text
        self.content = b""

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class _MultiServer:
    """Several jobs dispatched to builder #7; claims are slow and idempotent.

    ``claim_failures[job_id]`` = number of leading claims answered 503 (the
    job stays dispatched, as a lost/failed claim leaves it).
    """

    def __init__(self, job_ids, *, claim_latency=0.3, claim_failures=None):
        self.lock = threading.Lock()
        self.status = {j: "dispatched" for j in job_ids}
        self.claim_latency = claim_latency
        self.claim_failures = dict(claim_failures or {})
        self.claims: dict[int, int] = {j: 0 for j in job_ids}
        self.ok_claims: dict[int, int] = {j: 0 for j in job_ids}
        self.recipe_fetches: dict[str, int] = {}
        self.polls = 0

    def _job(self, jid):
        return {
            "id": jid,
            "recipe_name": f"r{jid}",
            "platform": "linux",
            "arch": "x86_64",
            "config": "release",
            "link": "shared",
            "org_slug": "",
            "status": self.status[jid],
            "builder_id": 7,
        }

    def client_cls(self):
        server = self

        class _C:
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
                    return _Resp(200, {"id": 7})
                for jid in server.status:
                    if url.endswith(f"/v1/builds/{jid}/claim"):
                        time.sleep(server.claim_latency)
                        with server.lock:
                            server.claims[jid] += 1
                            if server.claim_failures.get(jid, 0) > 0:
                                server.claim_failures[jid] -= 1
                                return _Resp(503, text="unavailable")
                            if server.status[jid] in ("dispatched", "pending"):
                                server.status[jid] = "running"
                            server.ok_claims[jid] += 1
                            return _Resp(200, server._job(jid))
                    if url.endswith(f"/v1/builds/{jid}/fail") or url.endswith(
                        f"/v1/builds/{jid}/complete"
                    ):
                        with server.lock:
                            server.status[jid] = "failed"
                            return _Resp(200, server._job(jid))
                return _Resp(200, {})

            def patch(self, url, json=None, **k):
                return _Resp(200, {})

            def get(self, url, **k):
                if url.endswith("/next-job") or url.endswith("/v1/builds/next-claimable"):
                    with server.lock:
                        server.polls += 1
                        for jid in sorted(server.status):
                            if server.status[jid] in ("dispatched", "pending"):
                                return _Resp(200, server._job(jid))
                    time.sleep(0.05)
                    return _Resp(204)
                if "/v1/recipes/" in url:
                    name = url.split("/v1/recipes/")[1].split("/")[0]
                    with server.lock:
                        server.recipe_fetches[name] = server.recipe_fetches.get(name, 0) + 1
                    return _Resp(404, text="no such recipe")
                return _Resp(404)

            def delete(self, url, **k):
                return _Resp(200, {})

        return _C


def _args(tmp_path, extra):
    return [
        "--server",
        "http://test",
        "--token",
        "t",
        "--name",
        "probe",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--max-jobs",
        "2",
        "--no-auto-capabilities",
        "--no-free-disk",
        "--work-dir",
        str(tmp_path / "wd"),
        "--recipe-cache-dir",
        str(tmp_path / "rc"),
        "--pidfile",
        str(tmp_path / "b.pid"),
    ] + extra


def test_failed_claim_is_retried_and_job_runs_once(monkeypatch, tmp_path):
    """A 503 on the claim releases the in-flight mark; the re-hand-out is
    admitted (no permanent blacklist), and the job is built exactly once."""
    srv = _MultiServer([42], claim_latency=0.2, claim_failures={42: 1})
    monkeypatch.setattr(httpx, "Client", srv.client_cls())
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--no-websocket", "--max-runtime", "4"]))
    assert r.exit_code == 0, r.output
    assert srv.claims[42] == 2, r.output
    assert srv.ok_claims[42] == 1
    assert srv.recipe_fetches.get("r42", 0) == 1


def test_two_dispatched_jobs_each_run_once(monkeypatch, tmp_path):
    """Head-of-line: job 42 (slow claim) sorts first; 43 must still start, once."""
    srv = _MultiServer([42, 43], claim_latency=0.5)
    monkeypatch.setattr(httpx, "Client", srv.client_cls())
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--no-websocket", "--max-runtime", "5"]))
    assert r.exit_code == 0, r.output
    assert srv.claims == {42: 1, 43: 1}, r.output
    assert srv.recipe_fetches == {"r42": 1, "r43": 1}


@pytest.mark.parametrize(
    "body",
    [None, [], "oops", {"status": "dispatched"}, ValueError("not json")],
    ids=["null", "list", "str", "no-id", "not-json"],
)
def test_a_200_that_is_not_a_job_does_not_stop_the_builder(monkeypatch, tmp_path, body):
    """next-job answering 200 with something that is not a job object (a
    proxy page, a truncated body) is skipped; the poll loop keeps running."""
    srv = _MultiServer([42])
    base_cls = srv.client_cls()

    class _C(base_cls):
        def get(self, url, **k):
            if url.endswith("/next-job"):
                r = _Resp(200)
                r._data = body
                return r
            return super().get(url, **k)

    monkeypatch.setattr(httpx, "Client", _C)
    monkeypatch.setattr(time, "sleep", lambda s, _real=time.sleep: _real(min(s, 0.05)))
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--no-websocket", "--max-runtime", "1"]))
    assert r.exit_code == 0, (r.output, repr(r.exception))
    assert "not a job" in r.output
    assert srv.claims[42] == 0


# -- WebSocket path ---------------------------------------------------------


def _install_fake_ws(monkeypatch, messages):
    """Fake websockets.sync.client delivering *messages* then idling."""

    class _WS:
        def __init__(self):
            self.q = list(messages)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):  # master's loop calls this
            pass

        def send(self, data):
            pass

        def recv(self, timeout=None):
            if self.q:
                return json.dumps(self.q.pop(0))
            time.sleep(min(timeout or 0.1, 0.1))
            raise TimeoutError

    client_mod = types.ModuleType("websockets.sync.client")
    client_mod.connect = lambda *a, **k: _WS()
    sync_mod = types.ModuleType("websockets.sync")
    sync_mod.client = client_mod
    ws_mod = types.ModuleType("websockets")
    ws_mod.sync = sync_mod
    monkeypatch.setitem(sys.modules, "websockets", ws_mod)
    monkeypatch.setitem(sys.modules, "websockets.sync", sync_mod)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", client_mod)


def test_ws_repeated_dispatch_runs_once(monkeypatch, tmp_path):
    srv = _MultiServer([42], claim_latency=0.5)
    job = srv._job(42)
    _install_fake_ws(
        monkeypatch,
        [{"type": "job.dispatch", "job": job}, {"type": "job.dispatch", "job": job}],
    )
    monkeypatch.setattr(httpx, "Client", srv.client_cls())
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--max-runtime", "2"]))
    assert r.exit_code == 0, r.output
    assert "WebSocket connected." in r.output, r.output
    assert srv.claims[42] == 1, r.output
    assert "ignoring the repeated dispatch" in r.output


def test_thread_start_failure_does_not_blacklist_the_job(monkeypatch, tmp_path):
    """If the job thread cannot start, the in-flight mark must not survive:
    otherwise next-job keeps handing the job back and it is refused forever.
    WS path: the exception is swallowed into the long-poll fallback."""
    srv = _MultiServer([42], claim_latency=0.1)
    job = srv._job(42)
    _install_fake_ws(monkeypatch, [{"type": "job.dispatch", "job": job}])
    monkeypatch.setattr(httpx, "Client", srv.client_cls())
    real_start = threading.Thread.start
    calls = {"n": 0}

    def flaky_start(self):
        if getattr(self, "_target", None) is not None and "_run_job_guarded" in getattr(
            self._target, "__name__", ""
        ):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("can't start new thread")
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", flaky_start)
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--max-runtime", "4"]))
    assert srv.ok_claims[42] == 1, r.output
    assert srv.recipe_fetches.get("r42", 0) == 1, r.output


def test_claim_whose_answer_is_lost_is_retried(monkeypatch, tmp_path):
    """The claim lands (job -> running) but the response is lost.  next-job
    never hands a running job back, so unless the builder re-claims (which is
    idempotent for it) the job sits running with nothing building it."""
    srv = _MultiServer([42], claim_latency=0.0)
    base_cls = srv.client_cls()
    lost = {"n": 0}

    class _C(base_cls):
        def post(self, url, **k):
            r = super().post(url, **k)
            if url.endswith("/v1/builds/42/claim") and lost["n"] == 0:
                lost["n"] += 1
                raise httpx.ReadTimeout("response lost")
            return r

    monkeypatch.setattr(httpx, "Client", _C)
    monkeypatch.setattr(time, "sleep", lambda s, _real=time.sleep: _real(min(s, 0.05)))
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--no-websocket", "--max-runtime", "2"]))
    assert r.exit_code == 0, r.output
    assert srv.recipe_fetches.get("r42", 0) == 1, r.output


def test_http_loop_survives_a_thread_that_cannot_start(monkeypatch, tmp_path):
    """Long-poll path: ``Thread.start()`` raising (a thread or process limit)
    must not end the main loop -- a BSD builder started from ``@reboot`` has
    no supervisor and would stay down until the next reboot.  The job stays
    dispatched and a later poll runs it, once."""
    srv = _MultiServer([42], claim_latency=0.0)
    monkeypatch.setattr(httpx, "Client", srv.client_cls())
    monkeypatch.setattr(time, "sleep", lambda s, _real=time.sleep: _real(min(s, 0.05)))
    real_start = threading.Thread.start
    calls = {"n": 0}

    def flaky_start(self):
        if "_run_job_guarded" in getattr(getattr(self, "_target", None), "__name__", ""):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("can't start new thread")
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", flaky_start)
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--no-websocket", "--max-runtime", "1.5"]))
    assert r.exit_code == 0, (r.output, repr(r.exception))
    assert "could not start a job thread" in r.output
    assert calls["n"] >= 2, r.output
    assert srv.ok_claims[42] == 1, r.output
    assert srv.recipe_fetches.get("r42", 0) == 1, r.output


class _LostClaimServer(_MultiServer):
    """Every claim answer is lost, though the first one lands; GET
    /v1/builds/{id} reports the job's row as the server holds it."""

    def __init__(self, *, holder):
        super().__init__([42], claim_latency=0.0)
        self.holder = holder
        self.gets = 0

    def client_cls(self):
        server = self
        base_cls = super().client_cls()

        class _C(base_cls):
            def post(self, url, **k):
                r = super().post(url, **k)
                if url.endswith("/v1/builds/42/claim"):
                    raise httpx.ReadTimeout("response lost")
                return r

            def get(self, url, **k):
                if url.endswith("/v1/builds/42"):
                    with server.lock:
                        server.gets += 1
                        row = server._job(42)
                    row["builder_id"] = server.holder
                    return _Resp(200, row)
                return super().get(url, **k)

        return _C


@pytest.mark.parametrize(("holder", "built"), [(7, True), (99, False)], ids=["ours", "theirs"])
def test_claim_lost_on_every_attempt_checks_who_holds_the_job(monkeypatch, tmp_path, holder, built):
    """The last claim attempt failing in transport does not mean no attempt
    landed.  next-job never hands a running job back, so a landed claim with
    nothing building it would sit "running" until the 2 h build timeout.  The
    builder asks the server and builds the job only if it holds it."""
    srv = _LostClaimServer(holder=holder)
    monkeypatch.setattr(httpx, "Client", srv.client_cls())
    monkeypatch.setattr(time, "sleep", lambda s, _real=time.sleep: _real(min(s, 0.05)))
    r = CliRunner().invoke(builder_run, _args(tmp_path, ["--no-websocket", "--max-runtime", "1.5"]))
    assert r.exit_code == 0, r.output
    assert srv.claims[42] == 3, r.output  # every attempt was made ...
    assert srv.gets >= 1, r.output  # ... then the job's holder looked up
    assert (srv.recipe_fetches.get("r42", 0) == 1) is built, r.output
    assert ("running under this builder" in r.output) is built
