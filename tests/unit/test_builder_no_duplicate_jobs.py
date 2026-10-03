"""One builder process must never run the same build job twice at once.

In the populate-server runs of 2026-09-24 .. 2026-10-03, 31 of 40 jobs that
landed on a max-jobs >= 2 builder (star-00 and every BSD builder) ran two
times concurrently on that builder -- one, ffmpeg-cli on star-00 (max-jobs
4), three times -- while the max-jobs 1 builder never did.  Each job log shows
two ``cvcpkg-job-<recipe>-*`` trees, two recipe extractions and two publishes
of one variant (ffmpeg-cli 7.1.2+cvc.2 linux was built twice with different
sha256s; the second publish lost the race and was silently skipped).

The cause is the HTTP long-poll loop.  ``next-job`` keeps returning a job for
as long as it is still ``dispatched``, and the builder only claims it (moving
it to ``running``) from inside the job thread.  After starting that thread
the loop polls again at once -- a builder with a free slot (max-jobs >= 2) is
handed the SAME job before its own claim has landed, starts a second thread,
and the second claim succeeds too: re-claiming a job the same builder already
holds is deliberately idempotent on the server (a builder whose claim
response was lost must be able to retry).  The drain path (``next-claimable``
+ a fixed ``--name`` claimant) has the identical window.

Production runs on this path exclusively: the cvcpkg.org proxy does not pass
WebSocket upgrades, so every builder falls back to long-poll.

The server is faked at the ``httpx.Client`` layer with that exact shape: the
job stays claimable until a (slow) claim lands, and every claim by this
builder answers 200.
"""

from __future__ import annotations

import signal
import threading
import time

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli._builder import builder_run

JOB_ID = 42


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``builder_run`` installs global SIGINT/SIGTERM handlers; neutralize them
    so invoking it in-process doesn't clobber the test runner's handlers."""
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
        return self._data


class _FakeServer:
    """Just enough of cvcpkg-server to reproduce the duplicate hand-out.

    One job, dispatched to builder #7.  ``next-job`` / ``next-claimable``
    return it while it is claimable.  A claim takes ``claim_latency`` seconds
    (auth + audit + UPDATE on the real server) and is idempotent for this
    builder, exactly like ``POST /v1/builds/{id}/claim``.  The recipe download
    404s, so a job that gets past its claim fails fast and reports /fail.
    """

    def __init__(self, *, claim_latency: float = 0.3, claim_status: str = "running"):
        self.lock = threading.Lock()
        self.status = "dispatched"
        self.claim_latency = claim_latency
        self.claim_status = claim_status
        self.claims = 0
        self.recipe_fetches = 0
        self.log: list[str] = []

    def _job(self) -> dict:
        return {
            "id": JOB_ID,
            "recipe_name": "zlib",
            "platform": "linux",
            "arch": "x86_64",
            "config": "release",
            "link": "shared",
            "org_slug": "",
            "status": self.status,
            "builder_id": 7,
        }

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
                    return _Resp(200, {"id": 7})
                if url.endswith(f"/v1/builds/{JOB_ID}/claim"):
                    time.sleep(server.claim_latency)
                    with server.lock:
                        server.claims += 1
                        if server.status in ("dispatched", "pending"):
                            server.status = server.claim_status
                        return _Resp(200, server._job())
                if url.endswith(f"/v1/builds/{JOB_ID}/fail") or url.endswith(
                    f"/v1/builds/{JOB_ID}/complete"
                ):
                    with server.lock:
                        server.status = "failed" if url.endswith("/fail") else "succeeded"
                        return _Resp(200, server._job())
                return _Resp(200, {})  # heartbeat

            def patch(self, url, json=None, **k):
                with server.lock:
                    server.log.append((json or {}).get("data", ""))
                return _Resp(200, {})

            def get(self, url, **k):
                if url.endswith("/next-job") or url.endswith("/v1/builds/next-claimable"):
                    with server.lock:
                        if server.status in ("dispatched", "pending"):
                            return _Resp(200, server._job())
                    time.sleep(0.05)  # stand-in for the long-poll wait
                    return _Resp(204)
                if "/v1/recipes/" in url:
                    with server.lock:
                        server.recipe_fetches += 1
                    return _Resp(404, text="no such recipe")
                return _Resp(404)

            def delete(self, url, **k):
                return _Resp(200, {})

        return _FakeClient


def _run(monkeypatch, tmp_path, server: _FakeServer, extra: list[str]):
    monkeypatch.setattr(httpx, "Client", server.client_cls())
    return CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            "t",
            "--name",
            "dup-probe",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--max-jobs",
            "2",
            "--no-websocket",
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


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(["--max-runtime", "2"], id="registered-next-job"),
        pytest.param(["--no-register"], id="drain-next-claimable"),
    ],
)
def test_job_handed_out_twice_runs_once(monkeypatch, tmp_path, mode):
    """The second hand-out of a job this process is already running is
    ignored: one claim, one recipe fetch, one job tree."""
    server = _FakeServer()
    result = _run(monkeypatch, tmp_path, server, mode)

    assert result.exit_code == 0, result.output
    downloads = sum(chunk.count("Downloading recipe 'zlib'") for chunk in server.log)
    assert server.claims == 1, (
        f"job {JOB_ID} was claimed {server.claims} times by one builder -- it ran "
        f"concurrently in {server.claims} threads.\n{result.output}"
    )
    assert server.recipe_fetches == 1
    assert downloads == 1


def test_claim_answering_a_non_running_status_is_not_built(monkeypatch, tmp_path):
    """``claim`` answers 200 and hands the row back for a job that is no
    longer claimable (cancelled while it was being handed out, or already
    finished).  That is a refusal, not a go-ahead: building it would publish
    a cancelled job and then flip it to succeeded."""
    server = _FakeServer(claim_latency=0.0, claim_status="cancelled")
    result = _run(monkeypatch, tmp_path, server, ["--max-runtime", "1"])

    assert result.exit_code == 0, result.output
    assert server.claims == 1
    assert server.recipe_fetches == 0, result.output
    assert not any("Downloading recipe" in chunk for chunk in server.log)
