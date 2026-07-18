"""`_wait_for_dags` — the wait loop behind `cvcpkg builds submit-dag --wait`.

The dev-cluster CI check relies on three properties here, each of which used to
be a way it hung or lied:

  * empty DAG list returns at once (``--skip-existing`` can drop every recipe;
    the old ``while True`` spun forever on an empty set);
  * ``--wait-timeout`` turns "still building after the window" into a distinct
    soft-pass exit code instead of the workflow's 45-min hard kill, which left
    the queued jobs orphaned on the cluster;
  * a real FAILURE is still reported even when the timeout fires -- a slow build
    next to a failed one must not become a pass.
"""

from __future__ import annotations

import pytest

from cvcpkg.cli import _builds
from cvcpkg.cli._builds import WAIT_TIMEOUT_EXIT_CODE, _wait_for_dags


class _FakeClock:
    """Monotonic clock that only advances when sleep() is called."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, secs):
        self.t += secs


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p


class _FakeClient:
    """Serves /v1/builds (list by dag) and /v1/builds/{id} from a job table.

    ``jobs`` maps id -> status; the same statuses are returned for both the
    list poll and the per-job re-check, so the test controls terminal-ness.
    """

    def __init__(self, jobs: dict[int, str]):
        self._jobs = jobs

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **k):
        if url.endswith("/v1/builds"):
            return _Resp(
                {
                    "jobs": [
                        {
                            "id": jid,
                            "status": st,
                            "recipe_name": f"r{jid}",
                            "platform": "linux",
                            "arch": "x86_64",
                        }
                        for jid, st in self._jobs.items()
                    ]
                }
            )
        jid = int(url.rsplit("/", 1)[-1])
        return _Resp({"status": self._jobs[jid], "recipe_name": f"r{jid}"})


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch):
    monkeypatch.setattr(_builds, "time", _FakeClock())


def _patch_httpx(monkeypatch, jobs):
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient(jobs))


def test_empty_dag_list_returns_immediately(monkeypatch):
    import httpx

    # Must not even construct a client -- and must not hang.
    def _boom(*a, **k):
        raise AssertionError("must not poll for an empty DAG list")

    monkeypatch.setattr(httpx, "Client", _boom)
    _wait_for_dags("http://s", "t", [])  # returns, no raise, no hang


def test_all_succeeded_does_not_raise(monkeypatch):
    _patch_httpx(monkeypatch, {1: "succeeded", 2: "succeeded"})
    _wait_for_dags("http://s", "t", ["dag"])


def test_a_failed_job_raises(monkeypatch):
    import click

    _patch_httpx(monkeypatch, {1: "succeeded", 2: "failed"})
    with pytest.raises(click.ClickException):
        _wait_for_dags("http://s", "t", ["dag"])


def test_timeout_with_still_building_is_soft_pass(monkeypatch):
    # Jobs never leave "running"; with a wait_timeout the loop must stop at the
    # deadline and exit WAIT_TIMEOUT_EXIT_CODE (not hang, not fail).
    _patch_httpx(monkeypatch, {1: "running", 2: "running"})
    with pytest.raises(SystemExit) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert exc.value.code == WAIT_TIMEOUT_EXIT_CODE


def test_timeout_still_reports_a_failure(monkeypatch):
    # A failed job alongside a still-building one must FAIL, not soft-pass --
    # the timeout must never mask a real failure.
    import click

    _patch_httpx(monkeypatch, {1: "failed", 2: "running"})
    with pytest.raises(click.ClickException):
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)


def test_no_timeout_and_all_terminal_succeeds(monkeypatch):
    # Without wait_timeout, a fully-terminal DAG returns normally.
    _patch_httpx(monkeypatch, {1: "succeeded"})
    _wait_for_dags("http://s", "t", ["dag"], wait_timeout=None)


def test_unschedulable_is_terminal_and_only_fails_when_strict(monkeypatch):
    _patch_httpx(monkeypatch, {1: "succeeded", 2: "unschedulable"})
    # tolerant: unschedulable counts as skipped, not a failure
    _wait_for_dags("http://s", "t", ["dag"], fail_on_unschedulable=False)
    # strict: unschedulable is a failure
    import click

    with pytest.raises(click.ClickException):
        _wait_for_dags("http://s", "t", ["dag"], fail_on_unschedulable=True)
