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
    """Serves /v1/builds (list by dag), /v1/builds/{id} and /v1/builders.

    ``jobs`` maps id -> status; the same statuses are returned for both the
    list poll and the per-job re-check, so the test controls terminal-ness.
    ``builders`` is the registry the deadline classification consults; the
    default (None) simulates an unreachable registry, which must be treated
    as "nothing could have claimed the job" (hard fail).
    """

    def __init__(self, jobs: dict[int, str], builders: list[dict] | None = None):
        self._jobs = jobs
        self._builders = builders

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **k):
        if url.endswith("/v1/builders"):
            if self._builders is None:
                raise OSError("registry unreachable")
            return _Resp({"builders": self._builders})
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
        return _Resp(
            {
                "status": self._jobs[jid],
                "recipe_name": f"r{jid}",
                "platform": "linux",
                "arch": "x86_64",
            }
        )


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch):
    monkeypatch.setattr(_builds, "time", _FakeClock())


def _patch_httpx(monkeypatch, jobs, builders=None):
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient(jobs, builders))


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


# ── Unclaimed jobs are not "still building" ─────────────────────────
#
# The soft pass exists for a heavy recipe that outruns the CI window while a
# builder is genuinely compiling it.  It must NOT cover a job no builder ever
# picked up: that means the run verified nothing, and reporting it green is
# how ca-bundle's dev-cluster check passed twice (pr-447, pr-457) while a
# 190 KB download sat unclaimed for the full 25-minute window.


def test_timeout_with_an_unclaimed_job_fails(monkeypatch):
    # "pending" = never dispatched to any builder.  Nothing was verified, so
    # this is a hard failure, not the soft pass a slow build gets.
    import click

    _patch_httpx(monkeypatch, {1: "pending"})
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "never claimed" in str(exc.value)


def test_timeout_with_a_dispatched_job_fails(monkeypatch):
    # "dispatched" means assigned but never started -- still nothing built.
    import click

    _patch_httpx(monkeypatch, {1: "dispatched"})
    with pytest.raises(click.ClickException):
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)


def test_unclaimed_outranks_a_genuinely_running_job(monkeypatch):
    # One job compiling does not excuse another that was never claimed; the
    # unclaimed one means that part of the DAG went unverified.
    import click

    _patch_httpx(monkeypatch, {1: "running", 2: "pending"})
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "never claimed" in str(exc.value)


def test_a_real_failure_still_outranks_an_unclaimed_job(monkeypatch):
    # Failure remains the most actionable signal: report it, not the stall.
    import click

    _patch_httpx(monkeypatch, {1: "failed", 2: "pending"})
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "did not succeed" in str(exc.value)


def test_running_only_is_still_a_soft_pass(monkeypatch):
    # The llvm case the soft pass was built for must keep working.
    _patch_httpx(monkeypatch, {1: "running", 2: "running"})
    with pytest.raises(SystemExit) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert exc.value.code == WAIT_TIMEOUT_EXIT_CODE


# ── Unclaimed-at-deadline classification consults the builder registry ──
#
# "Never claimed" has two very different causes.  A job queued behind an
# ONLINE builder that is merely at capacity is the same "submitted OK, the
# cluster will finish it" shape as a slow build — msys2's dev job pended for
# a full window on pr-465 itself solely because the one windows-capable
# builder (max_jobs=1) was busy.  But a job that nothing could EVER claim —
# every capable builder offline, or an idle online builder that simply never
# claimed it (the ca-bundle pathology) — must stay a hard failure.

_LINUX_BUILDER = {
    "name": "b-linux",
    "status": "online",
    "platform": "linux",
    "arch": "x86_64",
    "org_slug": "",
    "served_namespaces": [""],
    "capabilities": {},
    "current_jobs": 0,
    "max_jobs": 2,
}


def test_unclaimed_behind_busy_capable_builder_is_soft_pass(monkeypatch):
    busy = dict(_LINUX_BUILDER, current_jobs=2)
    _patch_httpx(monkeypatch, {1: "pending"}, builders=[busy])
    with pytest.raises(SystemExit) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert exc.value.code == WAIT_TIMEOUT_EXIT_CODE


def test_unclaimed_with_idle_online_builder_still_fails(monkeypatch):
    # The ca-bundle case: capacity was free the whole time and nothing claimed
    # the job — scheduler or builder gone silent.  Must NOT soft-pass.
    import click

    _patch_httpx(monkeypatch, {1: "pending"}, builders=[_LINUX_BUILDER])
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "never claimed" in str(exc.value)


def test_unclaimed_with_only_offline_builders_fails(monkeypatch):
    import click

    offline = dict(_LINUX_BUILDER, status="offline", current_jobs=2)
    _patch_httpx(monkeypatch, {1: "pending"}, builders=[offline])
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "never claimed" in str(exc.value)


def test_unclaimed_with_unreachable_registry_fails(monkeypatch):
    # If we cannot even ask who could serve the job, an unverified run must
    # not go green.  (builders=None makes the fake raise on /v1/builders.)
    import click

    _patch_httpx(monkeypatch, {1: "pending"}, builders=None)
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "never claimed" in str(exc.value)


def test_cross_platform_capability_counts_as_capable(monkeypatch):
    # A linux builder with a windows cross_platforms entry serves a windows
    # job (the sandipaws-wsl shape).  Busy -> the pending job is queued, soft
    # pass; the platform match must go through capabilities.cross_platforms.
    from cvcpkg.cli._builds import _builder_can_serve

    cross = dict(
        _LINUX_BUILDER,
        capabilities={"cross_platforms": [{"platform": "windows", "arch": "x86_64"}]},
    )
    win_job = {"platform": "windows", "arch": "x86_64", "org_slug": ""}
    assert _builder_can_serve(cross, win_job)
    assert not _builder_can_serve(_LINUX_BUILDER, win_job)


def test_queued_and_dead_together_still_fail(monkeypatch):
    # One job queued behind a busy builder does not excuse another that
    # nothing can serve: the dead one means part of the DAG went unverified.
    import click

    busy_linux = dict(_LINUX_BUILDER, current_jobs=2)

    class _TwoPlatformClient(_FakeClient):
        def get(self, url, **k):
            if url.rstrip("/").endswith("/2"):
                return _Resp(
                    {
                        "status": "pending",
                        "recipe_name": "r2",
                        "platform": "windows",
                        "arch": "x86_64",
                    }
                )
            return super().get(url, **k)

    import httpx

    monkeypatch.setattr(
        httpx, "Client", _TwoPlatformClient({1: "pending", 2: "pending"}, [busy_linux])
    )
    with pytest.raises(click.ClickException) as exc:
        _wait_for_dags("http://s", "t", ["dag"], wait_timeout=30)
    assert "never claimed" in str(exc.value)
    assert "r2" in str(exc.value)
