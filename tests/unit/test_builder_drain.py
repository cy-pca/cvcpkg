"""Ephemeral/drain-mode controls for ``cvcpkg builder run``.

Covers the two flags added for time-boxed CI runners (e.g. the macOS
GitHub-hosted drain):

* ``--exit-when-empty`` — exit 0 once the queue has no claimable job and
  none are in flight.
* ``--max-runtime`` — stop claiming after a wall-clock budget and exit.

The server is faked at the ``httpx.Client`` layer: registration succeeds,
``next-job`` always returns 204 (empty queue), and unregister succeeds.
"""

import signal

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli._builder import builder_run


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """``builder_run`` installs global SIGINT/SIGTERM handlers; neutralize them
    so invoking it in-process doesn't clobber the test runner's handlers."""
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast_drain_settle(monkeypatch):
    """Drain mode waits ``CVCPKG_DRAIN_SETTLE_SECS`` (default 20s, giving the
    scheduler time to dispatch) before concluding the queue is empty.  Zero it
    so the empty-queue tests exit immediately."""
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = {} if data is None else data
        self.text = text

    def json(self):
        return self._data


def _fake_client(get_status=204, calls=None):
    """A drop-in for httpx.Client that routes by URL suffix."""

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
            if calls is not None:
                calls.append(("POST", url))
            if url.endswith("/register"):
                return _Resp(200, {"id": 1})
            return _Resp(200, {})

        def get(self, url, **k):
            if calls is not None:
                calls.append(("GET", url))
            return _Resp(get_status)  # 204 == empty queue

        def delete(self, url, **k):
            if calls is not None:
                calls.append(("DELETE", url))
            return _Resp(200, {})

    return _FakeClient


_BASE_ARGS = [
    "--server",
    "http://test",
    "--token",
    "t",
    "--platform",
    "macos",
    "--arch",
    "x86_64",
    "--no-websocket",
]


def test_exit_when_empty_exits_cleanly(monkeypatch, tmp_path):
    """Empty queue (204) with nothing in flight → exit 0 in drain mode."""
    monkeypatch.setattr(httpx, "Client", _fake_client(get_status=204))
    result = CliRunner().invoke(
        builder_run,
        _BASE_ARGS
        + [
            "--name",
            "ci-drain",
            "--exit-when-empty",
            "--work-dir",
            str(tmp_path / "wd"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Queue empty" in result.output


def test_max_runtime_stops_and_exits(monkeypatch, tmp_path):
    """Without --exit-when-empty the 204 loop would run forever; the
    wall-clock budget must break it and exit 0."""
    monkeypatch.setattr(httpx, "Client", _fake_client(get_status=204))
    result = CliRunner().invoke(
        builder_run,
        _BASE_ARGS
        + [
            "--name",
            "ci-timebox",
            "--max-runtime",
            "0.5",
            "--work-dir",
            str(tmp_path / "wd"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Max runtime reached" in result.output


def test_drain_unregisters_on_exit(monkeypatch, tmp_path):
    """A draining runner should unregister itself (DELETE) on exit so it
    doesn't linger as a stale builder."""
    calls: list = []
    monkeypatch.setattr(httpx, "Client", _fake_client(get_status=204, calls=calls))
    result = CliRunner().invoke(
        builder_run,
        _BASE_ARGS
        + [
            "--name",
            "ci-drain",
            "--exit-when-empty",
            "--work-dir",
            str(tmp_path / "wd"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert any(method == "DELETE" for method, _ in calls)


def test_drain_settle_waits_before_exit(monkeypatch, tmp_path):
    """With a non-zero settle window, an empty queue does NOT exit on the
    first 204 — it waits, giving the scheduler time to dispatch a pending
    job to a just-registered drain builder (regression for the macOS drain
    orphaning jobs)."""
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "1")
    monkeypatch.setattr(httpx, "Client", _fake_client(get_status=204))

    import time as _time

    start = _time.time()
    runner = CliRunner()
    result = runner.invoke(
        builder_run,
        [
            "--server",
            "http://s.example",
            "--token",
            "t",
            "--name",
            "drain-settle",
            "--work-dir",
            str(tmp_path),
            "--exit-when-empty",
            "--no-websocket",
        ],
    )
    elapsed = _time.time() - start
    assert result.exit_code == 0, result.output
    # It must have waited out the ~1s settle window rather than exiting on
    # the first empty poll.
    assert elapsed >= 1.0, f"drain exited too fast ({elapsed:.2f}s); settle window not honored"
    assert "Queue empty" in result.output


def test_failed_claim_releases_slot(monkeypatch, tmp_path):
    """A job whose claim fails (>=400) must release its slot. Otherwise the
    slot leaks and a max-jobs=1 builder wedges at capacity, never polling
    again — the openbsd builders got stuck at 2/2 exactly this way.

    Setup: next-job hands out one job, then 204 forever; the claim POST
    returns 409. With the leak the builder would never poll next-job again
    (available==0) and would only exit via --max-runtime. Fixed, the slot is
    released, the next poll sees 204, and it exits 'Queue empty'."""
    job_given = {"n": 0}

    class _Client:
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
                return _Resp(200, {"id": 1})
            if url.endswith("/claim"):
                return _Resp(409, {"detail": "already claimed"})  # force early return
            return _Resp(200, {})

        def get(self, url, **k):
            if url.endswith("/next-job"):
                if job_given["n"] == 0:
                    job_given["n"] = 1
                    return _Resp(
                        200,
                        {
                            "id": 42,
                            "recipe_name": "zlib",
                            "platform": "macos",
                            "arch": "arm64",
                        },
                    )
                return _Resp(204)
            return _Resp(204)

        def delete(self, url, **k):
            return _Resp(200, {})

    monkeypatch.setattr(httpx, "Client", _Client)
    result = CliRunner().invoke(
        builder_run,
        _BASE_ARGS
        + [
            "--name",
            "leak-test",
            "--max-jobs",
            "1",
            "--exit-when-empty",
            "--max-runtime",
            "20",
            "--work-dir",
            str(tmp_path / "wd"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    # If the slot leaked, capacity would stay 0 and it could only exit via the
    # runtime budget.  The fix means it drains and exits on the empty queue.
    assert "Queue empty" in result.output, result.output
    assert "Max runtime reached" not in result.output, result.output
