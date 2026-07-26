# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import time
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._server import _api_request

# Build-job states that never change again.  "unschedulable" IS terminal:
# the server reaps a pending job to it when no registered builder covers
# its platform/arch (and cancels the job's dependents).  A waiter that
# leaves it out polls forever — this wedged the pr-recipe-build-dev CI
# runs (dag pr-223 hung 91 min; pr-226 needed a manual cancel).
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out", "unschedulable"})

# Exit code from `--wait --wait-timeout` when the budget elapses with jobs still
# building and NONE failed.  Distinct from 0 (all succeeded) and 1 (a job
# failed) so a caller can treat "submitted OK, still building" as a soft pass --
# e.g. a heavy recipe (llvm) that cannot finish inside a CI window.
WAIT_TIMEOUT_EXIT_CODE = 75

# A DAG's jobs may not be queryable the instant a waiter starts, so an empty
# result is only conclusive after this many consecutive polls.
_EMPTY_POLL_GRACE = 24  # × 5s ≈ 2 min

# Read timeout for a job's SSE log stream.  Must stay comfortably above the
# server's keepalive interval (_SSE_KEEPALIVE_SECONDS in server/app.py), which
# is what keeps a quiet build from tripping this.  Without any read timeout a
# stream dropped by an intermediary blocks its thread forever.
_FOLLOW_READ_TIMEOUT = 60.0

# Max concurrent log streams (was ThreadPoolExecutor(max_workers=32)).
_FOLLOW_MAX_STREAMS = 32

# ── Build job commands ──────────────────────────────────────────


@cli.group("builds")
def builds_group() -> None:
    """Manage remote build jobs."""


@builds_group.command("list")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--status", default=None, help="Filter by status.")
@click.option("--platform", default=None, help="Filter by platform.")
@click.option("--dag-id", default=None, help="Filter by DAG ID.")
@click.option("--recipe", "recipe_name", default=None, help="Filter by recipe name.")
@click.option("--limit", type=int, default=50, help="Max results.")
def builds_list(
    server: str,
    token: str,
    status: str | None,
    platform: str | None,
    dag_id: str | None,
    recipe_name: str | None,
    limit: int,
):
    """List build jobs."""
    import httpx

    params: dict[str, str | int] = {"limit": limit}
    if status:
        params["status"] = status
    if platform:
        params["platform"] = platform
    if dag_id:
        params["dag_id"] = dag_id
    if recipe_name:
        params["recipe_name"] = recipe_name
    url = f"{server.rstrip('/')}/v1/builds"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    jobs = data.get("jobs", [])
    if not jobs:
        click.echo("No build jobs found.")
        return
    click.echo(
        f"{'ID':>5}  {'Recipe':<20} {'Platform':<10} {'Config':<8} "
        f"{'Link':<7} {'Status':<10} {'DAG':>8}"
    )
    click.echo("-" * 78)
    for j in jobs:
        click.echo(
            f"{j['id']:>5}  {j['recipe_name']:<20} {j['platform']:<10} "
            f"{j['config']:<8} {j['link']:<7} {j['status']:<10} "
            f"{(j.get('dag_id') or '-'):>8}"
        )


@builds_group.command("info")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_info(job_id: int, server: str, token: str):
    """Show details for a build job."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/builds/{job_id}", token)
    click.echo(f"Build #{data['id']}: {data['recipe_name']}")
    click.echo(f"  Version:     {data.get('recipe_version') or '-'}")
    click.echo(f"  Platform:    {data['platform']}/{data['arch']}")
    click.echo(f"  Config:      {data['config']}")
    click.echo(f"  Link:        {data['link']}")
    click.echo(f"  Status:      {data['status']}")
    click.echo(f"  DAG:         {data.get('dag_id') or '-'}")
    click.echo(f"  Builder:     {data.get('builder_id') or 'unassigned'}")
    click.echo(f"  Priority:    {data.get('priority', 0)}")
    click.echo(f"  Submitted:   {data.get('submitted_at', 'unknown')}")
    click.echo(f"  Started:     {data.get('started_at') or '-'}")
    click.echo(f"  Finished:    {data.get('finished_at') or '-'}")
    if data.get("error_message"):
        click.echo(f"  Error:       {data['error_message']}")
    if data.get("result_archive_url"):
        click.echo(f"  Archive:     {data['result_archive_url']}")
    deps = data.get("depends_on", [])
    if deps:
        click.echo(f"  Depends on:  {', '.join(str(d) for d in deps)}")


@builds_group.command("cancel")
@click.argument("job_id", type=int)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Also cancel a running or dispatched job (recovery from a "
        "stuck builder). Cascades to downstream dependents."
    ),
)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_cancel(job_id: int, force: bool, server: str, token: str):
    """Cancel a build job."""
    url = f"{server.rstrip('/')}/v1/builds/{job_id}/cancel"
    if force:
        url += "?force=true"
    data = _api_request("post", url, token)
    status = data.get("status", "cancelled")
    cascaded = data.get("cascaded", 0)
    if cascaded:
        click.echo(f"Build #{job_id}: {status} (+{cascaded} downstream cancelled)")
    else:
        click.echo(f"Build #{job_id}: {status}")


@builds_group.command("cancel-dag")
@click.argument("dag_id")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_cancel_dag(dag_id: str, server: str, token: str):
    """Cancel all pending/dispatched jobs in a DAG.

    A trailing ``*`` matches by prefix, e.g. ``cancel-dag 'pr-288-*'`` cancels
    every sub-DAG of a PR run (submit-dag splits one DAG per platform/arch/
    config/link), including orphans left by superseded CI runs.
    """
    from urllib.parse import quote

    # dag_id goes in the path and may contain a '*' (prefix match); encode it.
    data = _api_request(
        "post", f"{server.rstrip('/')}/v1/builds/dag/{quote(dag_id, safe='')}/cancel", token
    )
    click.echo(f"DAG {dag_id}: {data.get('cancelled', 0)} jobs cancelled")


@builds_group.command("pause")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_pause(job_id: int, server: str, token: str):
    """Pause a pending or dispatched build job."""
    data = _api_request("post", f"{server.rstrip('/')}/v1/builds/{job_id}/pause", token)
    click.echo(f"Build #{job_id}: {data.get('status', 'paused')}")


@builds_group.command("resume")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_resume(job_id: int, server: str, token: str):
    """Resume a paused build job back to pending."""
    data = _api_request("post", f"{server.rstrip('/')}/v1/builds/{job_id}/resume", token)
    click.echo(f"Build #{job_id}: {data.get('status', 'pending')}")


@builds_group.command("pause-dag")
@click.argument("dag_id")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_pause_dag(dag_id: str, server: str, token: str):
    """Pause all pending/dispatched jobs in a DAG."""
    data = _api_request("post", f"{server.rstrip('/')}/v1/builds/dag/{dag_id}/pause", token)
    click.echo(f"DAG {dag_id}: {data.get('paused', 0)} jobs paused")


@builds_group.command("resume-dag")
@click.argument("dag_id")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_resume_dag(dag_id: str, server: str, token: str):
    """Resume all paused jobs in a DAG back to pending."""
    data = _api_request("post", f"{server.rstrip('/')}/v1/builds/dag/{dag_id}/resume", token)
    click.echo(f"DAG {dag_id}: {data.get('resumed', 0)} jobs resumed")


@builds_group.command("log")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--follow", "-f", is_flag=True, help="Follow log output (SSE stream).")
def builds_log(job_id: int, server: str, token: str, follow: bool):
    """View or follow the build log for a job."""
    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    if follow:
        url = f"{base}/v1/builds/{job_id}/log/stream"
        with httpx.Client(timeout=None) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise click.ClickException(f"server returned {resp.status_code}")
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        click.echo(line[6:])
                    elif line.startswith("event: done"):
                        break
    else:
        url = f"{base}/v1/builds/{job_id}/log"
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code == 404:
            raise click.ClickException(f"no log available for build job {job_id}")
        if resp.status_code >= 400:
            raise click.ClickException(f"server returned {resp.status_code}: {resp.text}")
        click.echo(resp.text, nl=False)


@builds_group.command("log-delete")
@click.argument("job_id", type=int)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_log_delete(job_id: int, server: str, token: str):
    """Delete the log for a build job (admin only)."""
    _api_request("delete", f"{server.rstrip('/')}/v1/builds/{job_id}/log", token)
    click.echo(f"Log for build #{job_id} deleted.")


@builds_group.command("follow-dag")
@click.argument("dag_id")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--wait-timeout",
    type=float,
    default=None,
    metavar="SECONDS",
    help=(
        "Stop following after SECONDS.  A failed job is still reported; if jobs "
        f"are merely still building, exit {WAIT_TIMEOUT_EXIT_CODE} (building) "
        "instead of burning the caller's whole job budget."
    ),
)
def builds_follow_dag(dag_id: str, server: str, token: str, wait_timeout: float | None):
    """Follow live build output for all jobs in a DAG.

    Multiplexes SSE log streams from every active job, interleaving
    lines with a [builder/recipe/platform/arch] prefix.  Useful in CI
    to get real-time build output from all remote builders.

    Exits 0 when all jobs succeed (or when the DAG has no jobs at all),
    1 if any job fails, and 2 if --wait-timeout elapses while jobs are
    still building.
    """
    import threading

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal = _TERMINAL_STATUSES
    print_lock = threading.Lock()
    stop_event = threading.Event()
    stream_slots = threading.Semaphore(_FOLLOW_MAX_STREAMS)
    followers: list[threading.Thread] = []
    seen_jobs: set[int] = set()
    final_statuses: dict[int, str] = {}
    # Cache builder_id → name so we only fetch once
    builder_names: dict[int, str] = {}

    def _resolve_builder_name(bid: int | None) -> str:
        if bid is None:
            return "?"
        if bid not in builder_names:
            try:
                with httpx.Client(timeout=10) as client:
                    resp = client.get(f"{base}/v1/builders/{bid}", headers=headers)
                if resp.status_code < 400:
                    builder_names[bid] = resp.json().get("name", f"#{bid}")
                else:
                    builder_names[bid] = f"#{bid}"
            except Exception:
                builder_names[bid] = f"#{bid}"
        return builder_names[bid]

    def _make_label(j: dict) -> str:
        builder = _resolve_builder_name(j.get("builder_id"))
        recipe = j.get("recipe_name", "?")
        plat = j.get("platform", "?")
        arch = j.get("arch", "?")
        return f"{builder}/{recipe}/{plat}/{arch}"

    def _follow_job(job_id: int, label: str):
        """Tail a single job's SSE stream, printing prefixed lines."""
        # Wait for a streaming slot, but stay responsive to shutdown.
        while not stream_slots.acquire(timeout=0.5):
            if stop_event.is_set():
                return
        url = f"{base}/v1/builds/{job_id}/log/stream"
        # A read timeout is essential: the server heartbeats the stream, so
        # silence this long means the connection is dead (an intermediary
        # dropped it).  timeout=None would block this thread forever.
        timeout = httpx.Timeout(_FOLLOW_READ_TIMEOUT, connect=10.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code >= 400:
                        with print_lock:
                            click.echo(f"  [{label}] log stream unavailable ({resp.status_code})")
                        return
                    for line in resp.iter_lines():
                        if stop_event.is_set():
                            break
                        if line.startswith("data: "):
                            with print_lock:
                                click.echo(f"[{label}] {line[6:]}")
                        elif line.startswith("event: done"):
                            break
        except Exception:
            pass  # best-effort
        finally:
            stream_slots.release()

    def _poll_and_spawn() -> str:
        """Poll for new jobs in the DAG and spawn followers.

        Returns ``"complete"`` when every job reached a terminal state,
        ``"empty"`` when the DAG never yielded any jobs, or ``"timeout"``
        when ``--wait-timeout`` elapsed first.
        """
        # Use prefix matching: append '*' so the server returns all
        # DAGs whose ID starts with the given string.
        query_dag = dag_id if "-" in dag_id and dag_id.count("-") >= 5 else f"{dag_id}*"
        deadline = None if wait_timeout is None else time.monotonic() + wait_timeout
        empty_polls = 0
        while True:
            # Checked at the top so every `continue` below is bounded too —
            # a persistently erroring server must not spin forever.
            if deadline is not None and time.monotonic() >= deadline:
                return "timeout"
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.get(
                        f"{base}/v1/builds",
                        headers=headers,
                        params={"dag_id": query_dag, "limit": 1000},
                    )
                if resp.status_code >= 400:
                    time.sleep(5)
                    continue
                jobs = resp.json().get("jobs", [])
            except Exception:
                time.sleep(5)
                continue

            # An empty result is expected briefly at startup, but if it
            # persists there is nothing to follow and waiting is pointless.
            if not jobs:
                empty_polls += 1
                if empty_polls >= _EMPTY_POLL_GRACE:
                    return "empty"
                time.sleep(5)
                continue
            empty_polls = 0

            all_terminal = True
            for j in jobs:
                jid = j["id"]
                status = j.get("status", "unknown")
                label = _make_label(j)

                if status in terminal:
                    # Record on *change*, not just first sight.  A job can leave
                    # a terminal state (a retried build re-runs and succeeds);
                    # keying on "have we seen it at all" froze the first verdict
                    # and reported a succeeded job as failed.
                    if final_statuses.get(jid) != status:
                        final_statuses[jid] = status
                        icon = "\u2713" if status == "succeeded" else "\u2717"
                        with print_lock:
                            click.echo(f"  {icon} #{jid} {label}: {status}")
                else:
                    all_terminal = False
                    # Back to running: drop the earlier verdict so the summary
                    # counts the outcome of the attempt that actually finishes.
                    final_statuses.pop(jid, None)

                # Start following active (non-pending) jobs we haven't seen
                if jid not in seen_jobs and status not in ("pending", *terminal):
                    seen_jobs.add(jid)
                    with print_lock:
                        click.echo(f"  \u25b6 #{jid} {label}: streaming log...")
                    # Daemon threads, not a ThreadPoolExecutor: pool threads
                    # are non-daemon and joined at interpreter exit, so a
                    # wedged stream would hold the process open even after
                    # every job finished.  These we can abandon.
                    t = threading.Thread(target=_follow_job, args=(jid, label), daemon=True)
                    t.start()
                    followers.append(t)

            if all_terminal:
                return "complete"
            time.sleep(5)

    click.echo(f"Following DAG: {dag_id}")
    click.echo("Waiting for jobs to appear...")

    outcome = _poll_and_spawn()

    # Followers are pure log tailing; once polling is done their output no
    # longer gates anything.  Give them a moment to flush, then abandon the
    # stragglers rather than letting a stuck stream hold the process open.
    stop_event.set()
    for t in followers:
        t.join(timeout=2.0)

    if outcome == "empty":
        click.echo(f"\nNo jobs found for DAG {dag_id} — nothing to follow.")
        return

    # Summary
    succeeded = sum(1 for s in final_statuses.values() if s == "succeeded")
    failed = sum(1 for s in final_statuses.values() if s != "succeeded")
    total = len(final_statuses)
    click.echo(f"\nDAG {dag_id}: {succeeded}/{total} succeeded, {failed} failed")

    if outcome == "timeout":
        unfinished = len(seen_jobs - set(final_statuses))
        click.echo(
            f"Timed out after {wait_timeout}s with {unfinished} job(s) still building.",
            err=True,
        )

    # A real failure outranks the timeout: it is the more actionable signal,
    # and the timeout code is reserved for "still building, nothing failed".
    if failed:
        raise SystemExit(1)
    if outcome == "timeout":
        raise SystemExit(WAIT_TIMEOUT_EXIT_CODE)


# ── Build-wait helpers ──────────────────────────────────────────────


def _wait_for_jobs(
    server: str, token: str, job_ids: list[int], *, wait_timeout: float | None = None
) -> None:
    """Poll until all job IDs reach a terminal state, printing updates.

    ``wait_timeout`` bounds the wait (0 = indefinitely); exceeding it exits
    ``WAIT_TIMEOUT_EXIT_CODE`` rather than polling forever on a job that never
    reaches a terminal state.
    """

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal = _TERMINAL_STATUSES
    pending = set(job_ids)
    deadline = None if wait_timeout is None else time.monotonic() + wait_timeout

    click.echo(f"\nWaiting for {len(pending)} job(s)...")
    with httpx.Client(timeout=30) as client:
        while pending:
            time.sleep(5)
            if deadline is not None and time.monotonic() >= deadline:
                click.echo(
                    f"Timed out after {wait_timeout}s with "
                    f"{len(pending)} job(s) still running: {sorted(pending)}",
                    err=True,
                )
                raise SystemExit(WAIT_TIMEOUT_EXIT_CODE)
            done_this_round = []
            for jid in list(pending):
                resp = client.get(f"{base}/v1/builds/{jid}", headers=headers)
                if resp.status_code >= 400:
                    continue
                info = resp.json()
                status = info.get("status", "unknown")
                recipe = info.get("recipe_name", "?")
                plat = info.get("platform", "?")
                if status in terminal:
                    icon = "\u2713" if status == "succeeded" else "\u2717"
                    click.echo(f"  {icon} #{jid} {recipe} ({plat}): {status}")
                    done_this_round.append(jid)
            for jid in done_this_round:
                pending.discard(jid)
            if pending:
                click.echo(f"  ... {len(pending)} job(s) still running", err=True)

    failed = []
    with httpx.Client(timeout=30) as client:
        for jid in job_ids:
            resp = client.get(f"{base}/v1/builds/{jid}", headers=headers)
            if resp.status_code < 400:
                info = resp.json()
                if info.get("status") != "succeeded":
                    failed.append(jid)
    if failed:
        raise click.ClickException(f"{len(failed)} job(s) failed: {failed}")
    click.echo(f"\nAll {len(job_ids)} job(s) succeeded.")


def _wait_for_dags(
    server: str,
    token: str,
    dag_ids: list[str],
    *,
    fail_on_unschedulable: bool = True,
    wait_timeout: float | None = None,
) -> None:
    """Poll until all jobs in the given DAGs reach terminal state.

    With ``fail_on_unschedulable=False`` (``submit-dag
    --allow-unschedulable``), jobs the server reaped as unschedulable
    count as skipped rather than failed — the caller explicitly accepted
    submitting combos no registered builder serves.

    With ``wait_timeout`` set, stop waiting after that many seconds.  A job that
    has already FAILED is still reported (raise) -- a slow build never masks a
    real failure.  If the only non-terminal jobs are still building, print them
    and exit ``WAIT_TIMEOUT_EXIT_CODE`` so a CI caller can treat a heavy build
    that outran its window as "submitted OK, building" rather than a
    timeout-kill (which would orphan the queued jobs).
    """

    import httpx

    if not dag_ids:
        # e.g. --skip-existing dropped every recipe as already-published.
        # Without this guard the poll loop below spins forever on an empty set.
        click.echo("\nNo DAGs to wait for (nothing was submitted).")
        return

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal = _TERMINAL_STATUSES

    click.echo(f"\nWaiting for {len(dag_ids)} DAG(s)...")
    all_job_ids: set[int] = set()
    finished: set[int] = set()
    deadline = None if wait_timeout is None else time.monotonic() + wait_timeout
    timed_out = False
    empty_polls = 0

    with httpx.Client(timeout=30) as client:
        while True:
            time.sleep(5)
            still_running = False
            listed_ok = False
            for did in dag_ids:
                resp = client.get(
                    f"{base}/v1/builds",
                    headers=headers,
                    params={"dag_id": did, "limit": 1000},
                )
                if resp.status_code >= 400:
                    continue
                listed_ok = True
                jobs = resp.json().get("jobs", [])
                for j in jobs:
                    jid = j["id"]
                    all_job_ids.add(jid)
                    status = j.get("status", "unknown")
                    if status in terminal:
                        if jid not in finished:
                            finished.add(jid)
                            icon = "\u2713" if status == "succeeded" else "\u2717"
                            click.echo(
                                f"  {icon} #{jid} {j['recipe_name']} "
                                f"({j['platform']}/{j.get('arch', '?')}): {status}"
                            )
                    else:
                        still_running = True

            if not still_running and all_job_ids:
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            # No jobs at all (e.g. --skip-existing dropped every recipe, or
            # the DAG IDs match nothing): there is nothing to wait for.
            # Only conclusive when the server actually answered — otherwise a
            # persistent 4xx/5xx would exit 0 and report a broken run green.
            if not all_job_ids and listed_ok:
                empty_polls += 1
                if empty_polls >= _EMPTY_POLL_GRACE:
                    click.echo("\nNo jobs found for the submitted DAG(s) — nothing to wait for.")
                    return
            if all_job_ids:
                click.echo(
                    f"  ... {len(all_job_ids) - len(finished)}/{len(all_job_ids)} job(s) remaining",
                    err=True,
                )

    # Re-check final states.
    failed_ids: list[int] = []
    skipped_ids: list[int] = []
    building: list[str] = []
    with httpx.Client(timeout=30) as client:
        for jid in all_job_ids:
            resp = client.get(f"{base}/v1/builds/{jid}", headers=headers)
            if resp.status_code >= 400:
                continue
            info = resp.json()
            status = info.get("status")
            if status == "succeeded":
                continue
            if status == "unschedulable" and not fail_on_unschedulable:
                skipped_ids.append(jid)
                continue
            if status not in terminal:
                # Still building -- only reachable when we stopped at the deadline.
                building.append(f"#{jid} {info.get('recipe_name', '?')}")
                continue
            failed_ids.append(jid)

    # A real failure is reported even if we timed out: a slow build alongside a
    # failed one must not turn the failure into a pass.
    if failed_ids:
        raise click.ClickException(f"{len(failed_ids)} job(s) did not succeed: {failed_ids}")
    if timed_out and not all_job_ids:
        # Never saw a single job, so the listing never succeeded (unreachable
        # server, expired token).  Falling through would print "All 0 job(s)
        # succeeded" and exit 0 — a run that built nothing, reported green.
        click.echo(
            f"\nNo jobs could be listed within {wait_timeout:.0f}s — the server "
            "never returned the submitted DAG(s).",
            err=True,
        )
        raise SystemExit(WAIT_TIMEOUT_EXIT_CODE)
    if timed_out and building:
        click.echo(
            f"\n{len(building)} job(s) still building after {wait_timeout:.0f}s "
            f"(submitted OK, continuing on the cluster): {', '.join(sorted(building))}"
        )
        raise SystemExit(WAIT_TIMEOUT_EXIT_CODE)
    if skipped_ids:
        click.echo(
            f"\n{len(all_job_ids) - len(skipped_ids)} job(s) succeeded, "
            f"{len(skipped_ids)} skipped as unschedulable (no registered builder): "
            f"{sorted(skipped_ids)}"
        )
    else:
        click.echo(f"\nAll {len(all_job_ids)} job(s) succeeded.")


@builds_group.command("submit")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--recipe", "recipe_name", required=True, help="Recipe name to build.")
@click.option("--platform", required=True, help="Target platform (e.g. linux, macos, windows).")
@click.option("--arch", required=True, help="Target architecture (e.g. x86_64, arm64).")
@click.option("--config", default="release", help="Build config (release or debug).")
@click.option("--link", default="shared", help="Link mode (shared or static).")
@click.option("--org", "org_slug", default="", help="Organization scope.")
@click.option("--priority", type=int, default=0, help="Job priority (higher = sooner).")
@click.option(
    "--timeout", "timeout_seconds", type=int, default=None, help="Per-job timeout (seconds)."
)
@click.option(
    "--wait", "-w", is_flag=True, help="Wait for the job to finish, printing status updates."
)
@click.option(
    "--wait-timeout",
    type=float,
    default=None,
    metavar="SECONDS",
    help=(
        "With --wait, stop waiting after SECONDS.  A failed job is still "
        f"reported; if the job is merely still building, exit "
        f"{WAIT_TIMEOUT_EXIT_CODE} (submitted OK, building)."
    ),
)
def builds_submit(
    server: str,
    token: str,
    recipe_name: str,
    platform: str,
    arch: str,
    config: str,
    link: str,
    org_slug: str,
    priority: int,
    timeout_seconds: int | None,
    wait: bool,
    wait_timeout: float | None,
):
    """Submit a single remote build job.

    Example: cvcpkg builds submit --recipe zlib --platform linux --arch x86_64
    """
    # wasm/wasi only support static linking.
    if platform in ("wasm", "wasi") and link != "static":
        link = "static"
        click.echo(f"  Note: forcing --link=static for {platform} (shared not supported)")

    body: dict = {
        "recipe_name": recipe_name,
        "platform": platform,
        "arch": arch,
        "config": config,
        "link": link,
        "org_slug": org_slug,
        "priority": priority,
    }
    if timeout_seconds is not None:
        body["timeout_seconds"] = timeout_seconds

    data = _api_request(
        "post",
        f"{server.rstrip('/')}/v1/builds",
        token,
        json=body,
    )
    click.echo(f"Submitted build #{data['id']}  {recipe_name} ({platform}/{arch}/{config}/{link})")
    click.echo(f"  Status: {data.get('status', 'pending')}")
    if data.get("dag_id"):
        click.echo(f"  DAG:    {data['dag_id']}")

    if wait:
        _wait_for_jobs(server, token, [data["id"]], wait_timeout=wait_timeout)


@builds_group.command("submit-dag")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option("--platform", required=True, help="Target platform(s), comma-separated.")
@click.option("--arch", required=True, help="Target architecture(s), comma-separated.")
@click.option("--config", default="release", help="Build config (release, debug, or 'all').")
@click.option("--link", default="shared", help="Link mode (shared, static, or 'all').")
@click.option("--org", "org_slug", default="", help="Organization scope.")
@click.option("--dag-id", default=None, help="Custom DAG ID (auto-generated if omitted).")
@click.option(
    "--wait", "-w", is_flag=True, help="Wait for all DAG jobs to finish, printing status updates."
)
@click.option(
    "--wait-timeout",
    type=float,
    default=None,
    metavar="SECONDS",
    help=(
        "With --wait, stop waiting after SECONDS.  A failed job is still "
        f"reported; if jobs are merely still building, exit {WAIT_TIMEOUT_EXIT_CODE} "
        "(submitted OK, building) instead of hanging until a CI/job timeout kills "
        "the wait and orphans the queue."
    ),
)
@click.option(
    "--recipes-dir",
    "recipes_dirs",
    type=click.Path(exists=True),
    multiple=True,
    help="Extra recipes directory (for dependency resolution).",
)
@click.option(
    "--no-default-recipes",
    is_flag=True,
    default=False,
    help="Ignore the auto-detected default recipes directory.",
)
@click.option(
    "--allow-unschedulable",
    is_flag=True,
    default=False,
    help=(
        "Submit jobs even for platform/arch combos no registered builder "
        "can serve.  By default such combos are skipped (the server would "
        "otherwise only reap them as unschedulable)."
    ),
)
@click.option(
    "--skip-existing/--no-skip-existing",
    default=True,
    help=(
        "Skip jobs whose exact variant (name, current recipe version, "
        "platform, arch, config, link) is already published on the server "
        "— e.g. imported from upstream by the populate loop.  Dependents "
        "of a skipped recipe still build; they fetch the published "
        "package as a dependency.  On by default ('fill the gaps'): a "
        "published variant is immutable, so rebuilding one is pure waste.  "
        "Pass --no-skip-existing to force a rebuild of variants that "
        "already exist."
    ),
)
@click.option(
    "--deps/--no-deps",
    "auto_deps",
    default=True,
    help=(
        "Auto-add unpublished, buildable dependencies of the named recipes "
        "as jobs so a dependency that is a catalog gap builds first "
        "(default on).  A dependency that is already published is left for "
        "the builder to install from the catalog, not rebuilt; an "
        "unpublished dependency with no buildable recipe is a hard error "
        "surfaced up front.  Use --no-deps to submit only the named recipes "
        "(assumes every dependency is already published)."
    ),
)
@click.argument("recipe_names", nargs=-1, required=True)
def builds_submit_dag(
    server: str,
    token: str,
    platform: str,
    arch: str,
    config: str,
    link: str,
    org_slug: str,
    dag_id: str | None,
    wait: bool,
    wait_timeout: float | None,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
    allow_unschedulable: bool,
    skip_existing: bool,
    auto_deps: bool,
    recipe_names: tuple[str, ...],
):
    """Submit a DAG of remote build jobs.

    Provide one or more recipe names as positional arguments.
    Use --config all / --link all to expand the build matrix.
    Dependencies are resolved from recipe.yaml files and jobs are
    ordered so that each recipe builds after its dependencies.

    By default (auto-deps) an UNpublished dependency that is a catalog
    gap is pulled into the DAG and built first, so naming just the leaf
    recipes is enough; already-published deps are installed from the
    catalog, not rebuilt.  An unpublished dep with no buildable recipe
    is reported up front instead of failing a build minutes later.  Pass
    --no-deps to submit only the named recipes.

    --skip-existing is also on by default: a named recipe (or auto-added
    dep) whose exact variant is already published is not rebuilt, so a
    run fills the gaps rather than rebuilding the world.  Pass
    --no-skip-existing to force a rebuild of variants that already exist.

    Example:

        cvcpkg builds submit-dag --platform linux --arch x86_64 \\
            --recipes-dir recipes zlib boost fftw
    """
    import yaml as _yaml

    from cvcpkg.cli._helpers import _resolve_recipes_dirs

    configs = ["release", "debug"] if config == "all" else [config]
    links = ["shared", "static"] if link == "all" else [link]
    platforms = [p.strip() for p in platform.split(",")]
    arches = [a.strip() for a in arch.split(",")]

    # ── Load recipe metadata for dependency resolution ──────────
    rdirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default_recipes)

    # Build recipe_name → recipe.yaml data mapping (later dirs win)
    recipe_data: dict[str, dict] = {}
    recipe_paths: dict[str, Path] = {}
    for rdir in rdirs:
        if not rdir.is_dir():
            continue
        for rpath in sorted(rdir.iterdir()):
            if not rpath.is_dir() or rpath.name.startswith(("_", ".")):
                continue
            yaml_path = rpath / "recipe.yaml"
            if yaml_path.is_file():
                recipe_data[rpath.name] = _yaml.safe_load(yaml_path.read_text())
                recipe_paths[rpath.name] = rpath

    def _dep_names(name: str) -> list[str]:
        """Extract runtime + build + host_tools dependency names from a recipe."""
        data = recipe_data.get(name, {})
        deps_block = data.get("depends", {})
        names: list[str] = []
        # Include host_tools so the DAG scheduler orders host prerequisites
        # (cmake, ninja, meson, ...) before recipes that depend on them.
        for key in ("runtime", "build", "host_tools"):
            for dep in deps_block.get(key, []) or []:
                if isinstance(dep, str):
                    names.append(dep)
                elif isinstance(dep, dict):
                    names.append(dep["name"])
        return names

    def _has_platform_entry(name: str, plat: str) -> bool:
        """Check if a recipe has a build matrix entry for the platform."""
        data = recipe_data.get(name, {})
        matrix = data.get("build", {}).get("matrix", [])
        return any(entry.get("platform") in (plat, "any") for entry in matrix)

    def _is_any(name: str) -> bool:
        """True if *name* is platform-independent (every matrix entry is 'any').

        Such a recipe ships one noarch bundle valid on every host, so it is
        scheduled once (platform=any/arch=noarch) instead of fanned out per
        concrete platform.
        """
        data = recipe_data.get(name, {})
        matrix = data.get("build", {}).get("matrix", [])
        return bool(matrix) and all(entry.get("platform") == "any" for entry in matrix)

    # Valid platform→arch pairings.  wasm32 only pairs with wasm/wasi.
    _wasm_arches = {"wasm32"}
    _wasm_platforms = {"wasm", "wasi"}
    # Platforms that only support static linking (no shared libraries).
    _static_only_platforms = {"wasm", "wasi", "cosmo"}

    # ── Drop combos no registered builder can serve ──────────────
    # The server dispatches a job only to a builder whose platform+arch
    # matches directly or via a cross-build capability.  Query the
    # server's builder registry up front and skip combos nothing can
    # ever claim (e.g. an arm64 arch on a platform that only has x86_64
    # builders), so we don't create jobs that would only sit pending
    # until the server reaps them.  Fail open: if the registry can't be
    # read we submit everything and let the server's reaper clean up.
    _supported_targets: set[tuple[str, str]] = set()
    _supported_platforms: set[str] = set()  # legacy platform-only cross targets
    _builder_check = not allow_unschedulable
    if _builder_check:
        import httpx as _httpx

        try:
            with _httpx.Client(timeout=30) as _c:
                _resp = _c.get(
                    f"{server.rstrip('/')}/v1/builders",
                    headers={"Authorization": f"Bearer {token}"},
                )
            _resp.raise_for_status()
            for _b in _resp.json().get("builders", []):
                _bp, _ba = _b.get("platform"), _b.get("arch")
                if _bp and _ba:
                    _supported_targets.add((_bp, _ba))
                for _cp in (_b.get("capabilities") or {}).get("cross_platforms", []) or []:
                    if isinstance(_cp, dict) and _cp.get("platform") and _cp.get("arch"):
                        _supported_targets.add((_cp["platform"], _cp["arch"]))
                    elif isinstance(_cp, str):
                        _supported_platforms.add(_cp)
        except Exception as _e:  # noqa: BLE001 — best-effort, must not block submit
            click.echo(
                f"  Warning: could not read builder registry ({_e}); submitting all "
                "combos (server will reap any unschedulable jobs)."
            )
            _builder_check = False

    def _has_builder(plat: str, ar: str) -> bool:
        return (plat, ar) in _supported_targets or plat in _supported_platforms

    # ── Published-variant set for --skip-existing / auto-deps ────
    # One paged listing up front; yanked packages are excluded by the
    # server default, so a yanked variant is rebuilt rather than skipped.
    # Both --skip-existing (drop already-published named recipes) and
    # auto-deps (only pull UNpublished deps into the DAG) need to know
    # what the catalog already carries, so fetch it for either.
    _published: set[tuple[str, str, str, str, str, str]] = set()
    _published_ok = False
    if skip_existing or auto_deps:
        import httpx as _httpx

        try:
            with _httpx.Client(timeout=60) as _c:
                _offset = 0
                while True:
                    _resp = _c.get(
                        f"{server.rstrip('/')}/v1/packages",
                        params={"limit": 1000, "offset": _offset},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    _resp.raise_for_status()
                    _data = _resp.json()
                    _batch = _data.get("packages", [])
                    for _p in _batch:
                        if not _p.get("archive_url"):
                            continue  # placeholder rows have no artifacts
                        _published.add(
                            (
                                _p.get("name", ""),
                                _p.get("version", ""),
                                _p.get("platform", ""),
                                _p.get("arch", ""),
                                _p.get("build_type", ""),
                                _p.get("link", ""),
                            )
                        )
                    _offset += len(_batch)
                    if not _batch or _offset >= int(_data.get("total", 0)):
                        break
            _published_ok = True
        except Exception as _e:  # noqa: BLE001 — best-effort, must not block submit
            click.echo(
                f"  Warning: could not read published packages ({_e}); "
                "submitting the named recipes only (no --skip-existing "
                "filtering, no dependency auto-add)."
            )
            skip_existing = False
            auto_deps = False

    def _full_version(name: str) -> str:
        """The version this recipe builds to (matches manifest/publish form)."""
        block = recipe_data.get(name, {}).get("recipe", {})
        upstream_v = str(block.get("upstream_version", "0.0.0"))
        rev = int(block.get("cvc_revision", 1))
        return f"{upstream_v}+cvc.{rev}"

    def _job_timeout(name: str) -> int | None:
        """Per-recipe build timeout (``build.timeout_seconds``), if declared.

        The server reaper honours a per-job ``timeout_seconds``; propagating
        the recipe's value lets a genuinely long build (e.g. llvm) exceed the
        server's default reap timeout without a global change.
        """
        t = recipe_data.get(name, {}).get("build", {}).get("timeout_seconds")
        return int(t) if t is not None else None

    def _closure(seeds: list[str]) -> set[str]:
        """Transitive dependency closure of *seeds* (runtime + build +
        host_tools edges), following only recipes we can see.

        Returns the dependency names, excluding the seeds themselves.  A dep
        with no recipe of its own is returned (so the caller can flag an
        unbuildable gap) but not traversed further.
        """
        seen: set[str] = set()
        queue = [d for s in seeds for d in _dep_names(s)]
        while queue:
            n = queue.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in recipe_data:
                queue.extend(_dep_names(n))
        seen.difference_update(seeds)
        return seen

    # Partition the requested recipes: platform-independent ('any') recipes are
    # scheduled once as a single noarch DAG (below); everything else fans out
    # per concrete platform/arch.  Keeping the 'any' recipes out of the
    # per-platform DAGs is what prevents the arch-pinned mispublish (one
    # linux-x86_64 bundle, one macos-arm64 bundle, ...) of a noarch package.
    any_names = [n for n in recipe_names if _is_any(n)]
    concrete_names = [n for n in recipe_names if not _is_any(n)]

    dag_ids: list[str] = []
    for plat in platforms:
        for ar in arches:
            # Skip invalid platform/arch combos
            if ar in _wasm_arches and plat not in _wasm_platforms:
                continue
            if plat in _wasm_platforms and ar not in _wasm_arches:
                continue
            # Skip combos no registered builder can serve.
            if _builder_check and not _has_builder(plat, ar):
                click.echo(f"  Skipping {plat}/{ar}: no registered builder can serve it")
                continue
            for cfg in configs:
                for lnk in links:
                    # wasm/wasi/cosmo only support static linking.
                    if plat in _static_only_platforms and lnk != "static":
                        continue
                    # Filter recipes: skip those with no matrix entry
                    eligible = [n for n in concrete_names if _has_platform_entry(n, plat)]
                    skipped = set(concrete_names) - set(eligible)
                    if skipped:
                        click.echo(
                            f"  Skipping {len(skipped)} recipe(s) "
                            f"with no {plat} matrix: {', '.join(sorted(skipped))}"
                        )

                    # --skip-existing: drop recipes whose exact variant is
                    # already published.  Dependents keep building — they
                    # install the published package as a dependency.
                    if skip_existing and eligible:
                        satisfied = [
                            n
                            for n in eligible
                            if (n, _full_version(n), plat, ar, cfg, lnk) in _published
                        ]
                        if satisfied:
                            click.echo(
                                f"  Skipping {len(satisfied)} already-published recipe(s) "
                                f"for {plat}/{ar}/{cfg}/{lnk}: {', '.join(sorted(satisfied))}"
                            )
                            eligible = [n for n in eligible if n not in set(satisfied)]

                    if not eligible:
                        click.echo(f"  No eligible recipes for {plat}/{ar}/{cfg}/{lnk}")
                        continue

                    # ── Auto-add unpublished, buildable dependencies ─────
                    # submit-dag otherwise builds ONLY the recipes it is
                    # given and assumes every dependency is already published
                    # (the builder installs deps from the catalog at build
                    # time).  When a dependency is an unpublished catalog GAP
                    # there is no bundle to install, so the dependent fails
                    # late at install/configure.  Pull each such dep into the
                    # DAG so it builds first (ordered ahead via depends_on).
                    # A dep that is already published is left for the builder
                    # to fetch; an unpublished dep with no buildable recipe is
                    # a hard error surfaced up front.
                    if auto_deps and _published_ok:
                        being_built = set(eligible)
                        added: list[str] = []
                        cross_noarch: list[str] = []
                        unbuildable: list[str] = []
                        for dep in sorted(_closure(eligible)):
                            if dep in being_built:
                                continue
                            has_recipe = dep in recipe_data
                            # Already in the catalog → builder installs it.
                            if has_recipe and (
                                (dep, _full_version(dep), plat, ar, cfg, lnk) in _published
                                or (dep, _full_version(dep), "any", "noarch", cfg, lnk)
                                in _published
                            ):
                                continue
                            if not has_recipe:
                                # No recipe to build it with: satisfiable only
                                # if the catalog already carries it for this
                                # target (its version is unknown without a
                                # recipe, so match on name + platform + arch).
                                if not any(
                                    p[0] == dep and p[2] in (plat, "any") and p[3] in (ar, "noarch")
                                    for p in _published
                                ):
                                    unbuildable.append(dep)
                                continue
                            if _is_any(dep):
                                # A noarch dep belongs in a separate any/noarch
                                # DAG; its edge is not expressible here, so flag
                                # it rather than silently mis-order.
                                cross_noarch.append(dep)
                                continue
                            if not _has_platform_entry(dep, plat):
                                # Has a recipe but no build for this platform:
                                # the dep doesn't apply here (a recipe's own
                                # cross-platform deps are its concern, e.g. a
                                # unix-only ncurses under a windows build), so
                                # skip it rather than block the whole submit —
                                # matching the behaviour before auto-deps.
                                continue
                            eligible.append(dep)
                            being_built.add(dep)
                            added.append(dep)
                        if added:
                            click.echo(
                                f"  Auto-added {len(added)} unpublished "
                                f"dependency(ies) for {plat}/{ar}/{cfg}/{lnk}: "
                                f"{', '.join(added)}"
                            )
                        if cross_noarch:
                            uniq = sorted(set(cross_noarch))
                            click.echo(
                                f"  Warning: {len(uniq)} unpublished noarch "
                                f"dependency(ies) cannot be scheduled inside this "
                                f"concrete DAG — submit them explicitly so they "
                                f"build in a separate any/noarch DAG: "
                                f"{', '.join(uniq)}"
                            )
                        if unbuildable:
                            uniq = sorted(set(unbuildable))
                            raise click.ClickException(
                                f"{plat}/{ar}: unpublished dependency(ies) with no "
                                f"buildable recipe: {', '.join(uniq)}. Publish them, "
                                f"add a recipe, or pass --no-deps to submit only the "
                                f"named recipes."
                            )

                    # Build name→index mapping for depends_on resolution
                    name_to_idx: dict[str, int] = {name: idx for idx, name in enumerate(eligible)}

                    jobs = []
                    for name in eligible:
                        dep_indices = list(
                            dict.fromkeys(
                                name_to_idx[dep_name]
                                for dep_name in _dep_names(name)
                                if dep_name in name_to_idx
                            )
                        )
                        job: dict = {
                            "recipe_name": name,
                            "platform": plat,
                            "arch": ar,
                            "config": cfg,
                            "link": lnk,
                            "org_slug": org_slug,
                            "depends_on": dep_indices,
                        }
                        _t = _job_timeout(name)
                        if _t is not None:
                            job["timeout_seconds"] = _t
                        jobs.append(job)

                    body: dict = {"jobs": jobs}
                    if dag_id:
                        body["dag_id"] = f"{dag_id}-{plat}-{ar}-{cfg}-{lnk}"

                    data = _api_request(
                        "post",
                        f"{server.rstrip('/')}/v1/builds/dag",
                        token,
                        json=body,
                    )
                    dag_ids.append(data["dag_id"])
                    click.echo(
                        f"DAG {data['dag_id']}: {data['total']} jobs ({plat}/{ar}/{cfg}/{lnk})"
                    )

    # ── Schedule platform-independent (noarch) recipes ONCE ──────────
    # A `platform: any` recipe builds one bundle valid everywhere, so it is
    # submitted as a single any/noarch DAG (per config/link) rather than once
    # per target platform.  The server routes these to a builder on the noarch
    # build target (see _choose_builder) -- the reference platform that has the
    # interpreter/toolchain deps -- which builds natively and publishes the
    # result as platform=any/arch=noarch (see pack_recipe).
    if any_names:
        from cvcpkg.platform import noarch_build_target

        _noarch_target = noarch_build_target()
        # Only skip when we positively know no builder can build noarch (the
        # reference target is unserved); fail open if the registry was unread.
        if _builder_check and _noarch_target not in _supported_targets:
            click.echo(
                f"  Skipping {len(any_names)} noarch recipe(s): no registered builder "
                f"for the noarch build target {_noarch_target[0]}/{_noarch_target[1]}"
            )
        else:
            for cfg in configs:
                for lnk in links:
                    eligible = list(any_names)

                    # --skip-existing: drop noarch variants already published.
                    if skip_existing and eligible:
                        satisfied = [
                            n
                            for n in eligible
                            if (n, _full_version(n), "any", "noarch", cfg, lnk) in _published
                        ]
                        if satisfied:
                            click.echo(
                                f"  Skipping {len(satisfied)} already-published noarch "
                                f"recipe(s) for {cfg}/{lnk}: {', '.join(sorted(satisfied))}"
                            )
                            eligible = [n for n in eligible if n not in set(satisfied)]

                    if not eligible:
                        click.echo(f"  No eligible noarch recipes for {cfg}/{lnk}")
                        continue

                    name_to_idx = {name: idx for idx, name in enumerate(eligible)}
                    jobs = []
                    for name in eligible:
                        # Only edges to other noarch recipes in this DAG are
                        # expressed; a concrete build dep (e.g. python312) is
                        # built elsewhere and fetched at build time.
                        dep_indices = list(
                            dict.fromkeys(
                                name_to_idx[dep_name]
                                for dep_name in _dep_names(name)
                                if dep_name in name_to_idx
                            )
                        )
                        job: dict = {
                            "recipe_name": name,
                            "platform": "any",
                            "arch": "noarch",
                            "config": cfg,
                            "link": lnk,
                            "org_slug": org_slug,
                            "depends_on": dep_indices,
                        }
                        _t = _job_timeout(name)
                        if _t is not None:
                            job["timeout_seconds"] = _t
                        jobs.append(job)

                    body: dict = {"jobs": jobs}
                    if dag_id:
                        body["dag_id"] = f"{dag_id}-any-noarch-{cfg}-{lnk}"

                    data = _api_request(
                        "post",
                        f"{server.rstrip('/')}/v1/builds/dag",
                        token,
                        json=body,
                    )
                    dag_ids.append(data["dag_id"])
                    click.echo(
                        f"DAG {data['dag_id']}: {data['total']} jobs (any/noarch/{cfg}/{lnk})"
                    )

    if wait:
        _wait_for_dags(
            server,
            token,
            dag_ids,
            fail_on_unschedulable=not allow_unschedulable,
            wait_timeout=wait_timeout,
        )


@builds_group.command("purge")
@click.option(
    "--older-than",
    "older_than",
    required=True,
    help="Age threshold, e.g. '30d' (days).",
)
@click.option("--status", default=None, help="Only purge jobs with this status (e.g. 'failed').")
@click.option(
    "--delete-logs/--keep-logs", default=True, help="Also delete log files (default: yes)."
)
@click.option(
    "--delete-jobs/--logs-only",
    "delete_jobs",
    default=False,
    help="Delete entire job rows, not just logs (default: logs only).",
)
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
def builds_purge(
    older_than: str,
    status: str | None,
    delete_logs: bool,
    delete_jobs: bool,
    server: str,
    token: str,
):
    """Purge old build logs/jobs (admin only).

    Example: cvcpkg builds purge --older-than 30d --status failed
    """
    import re

    import httpx

    m = re.match(r"^(\d+)d$", older_than)
    if not m:
        raise click.ClickException("--older-than must be in the form '<N>d', e.g. '30d'")
    days = int(m.group(1))

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, str | int | bool] = {
        "older_than_days": days,
        "delete_logs": delete_logs,
    }
    if status:
        params["status"] = status

    if delete_jobs:
        endpoint = f"{base}/v1/admin/purge/builds"
    else:
        endpoint = f"{base}/v1/admin/gc/logs"

    with httpx.Client(timeout=120) as client:
        resp = client.post(endpoint, headers=headers, params=params)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")
    data = resp.json()
    what = "jobs" if delete_jobs else "logs"
    click.echo(f"Purged {data.get('purged', 0)} {what} older than {days}d.")


@builds_group.command("monitor")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    required=True,
    metavar="URL",
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    required=True,
    help="Bearer token.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--interval",
    type=float,
    default=5.0,
    help="Refresh interval in seconds (default: 5).",
)
@click.option("--dag-id", default=None, help="Show only jobs from this DAG.")
def builds_monitor(server: str, token: str, interval: float, dag_id: str | None):
    """Live monitor of builders and build jobs (like top).

    Refreshes the terminal with current builder status and active/recent
    jobs.  Press Ctrl+C to exit.

    \b
    Example:
      cvcpkg builds monitor
      cvcpkg builds monitor --interval 2
      cvcpkg builds monitor --dag-id populate-20260604-190000
    """
    import shutil

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal_states = {"succeeded", "failed", "cancelled", "timed_out"}

    def _fetch_builders(client: httpx.Client) -> list[dict]:
        resp = client.get(f"{base}/v1/builders", headers=headers)
        if resp.status_code >= 400:
            return []
        return resp.json().get("builders", [])

    def _fetch_jobs(client: httpx.Client) -> list[dict]:
        params: dict[str, str | int] = {"limit": 200}
        if dag_id:
            params["dag_id"] = dag_id
        resp = client.get(f"{base}/v1/builds", headers=headers, params=params)
        if resp.status_code >= 400:
            return []
        return resp.json().get("jobs", [])

    def _render(builders: list[dict], jobs: list[dict], cols: int) -> str:
        lines: list[str] = []
        now_str = time.strftime("%H:%M:%S")

        # Header
        lines.append(f"cvcpkg builds monitor — {base} — {now_str}")
        lines.append("")

        # Builder summary
        online = [b for b in builders if b.get("status") == "online"]
        offline = [b for b in builders if b.get("status") != "online"]
        total_cap = sum(b.get("max_jobs", 0) for b in online)
        total_cur = sum(b.get("current_jobs", 0) for b in online)
        lines.append(
            f"Builders: {len(online)} online, {len(offline)} offline  "
            f"| Capacity: {total_cur}/{total_cap} slots in use"
        )
        lines.append("")

        # Builder table
        hdr = f"  {'Name':<18} {'Platform':<10} {'Arch':<8} {'Jobs':>4}/{'':<4} {'Status':<8}"
        lines.append(hdr)
        lines.append("  " + "-" * (len(hdr) - 2))
        for b in sorted(builders, key=lambda x: x.get("name", "")):
            status = b.get("status", "?")
            st_icon = "\u25cf" if status == "online" else "\u25cb"
            name = b.get("name", "?")[:18]
            plat = b.get("platform", "?")[:10]
            arch = b.get("arch", "?")[:8]
            cur = b.get("current_jobs", 0)
            mx = b.get("max_jobs", 0)
            lines.append(f"  {st_icon} {name:<17} {plat:<10} {arch:<8} {cur:>3}/{mx:<3}  {status}")
        lines.append("")

        # Job summary
        active = [j for j in jobs if j.get("status") not in terminal_states]
        done = [j for j in jobs if j.get("status") in terminal_states]
        succeeded = sum(1 for j in done if j.get("status") == "succeeded")
        failed = sum(1 for j in done if j.get("status") == "failed")
        lines.append(
            f"Jobs: {len(active)} active, {succeeded} succeeded, {failed} failed, {len(jobs)} total"
        )
        lines.append("")

        # Active jobs table
        if active:
            lines.append(f"  {'ID':>5}  {'Recipe':<18} {'Platform':<10} {'Status':<12} {'Builder'}")
            lines.append("  " + "-" * 65)
            for j in active[:30]:
                jid = j.get("id", "?")
                recipe = (j.get("recipe_name") or "?")[:18]
                plat = (j.get("platform") or "?")[:10]
                st = (j.get("status") or "?")[:12]
                builder = (j.get("builder_name") or j.get("assigned_builder") or "-")[:15]
                lines.append(f"  {jid:>5}  {recipe:<18} {plat:<10} {st:<12} {builder}")
            if len(active) > 30:
                lines.append(f"  ... and {len(active) - 30} more active jobs")
        else:
            lines.append("  No active jobs.")

        # Recent completed
        if done:
            lines.append("")
            lines.append("  Recent completed:")
            for j in done[:10]:
                jid = j.get("id", "?")
                recipe = (j.get("recipe_name") or "?")[:18]
                st = j.get("status", "?")
                icon = "\u2713" if st == "succeeded" else "\u2717"
                lines.append(f"    {icon} #{jid} {recipe}: {st}")

        lines.append("")
        lines.append("Press Ctrl+C to exit.")
        return "\n".join(lines)

    click.echo("Starting build monitor... (Ctrl+C to exit)")
    try:
        with httpx.Client(timeout=15) as client:
            while True:
                builders = _fetch_builders(client)
                jobs = _fetch_jobs(client)
                cols = shutil.get_terminal_size((80, 24)).columns

                output = _render(builders, jobs, cols)
                # Clear screen and redraw
                click.echo("\033[2J\033[H", nl=False)
                click.echo(output)
                time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nMonitor stopped.")
