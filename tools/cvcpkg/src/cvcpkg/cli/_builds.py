"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import time
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._server import _api_request

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
    """Cancel all pending/dispatched jobs in a DAG."""
    data = _api_request("post", f"{server.rstrip('/')}/v1/builds/dag/{dag_id}/cancel", token)
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
def builds_follow_dag(dag_id: str, server: str, token: str):
    """Follow live build output for all jobs in a DAG.

    Multiplexes SSE log streams from every active job, interleaving
    lines with a [builder/recipe/platform/arch] prefix.  Useful in CI
    to get real-time build output from all remote builders.

    Exits with code 0 when all jobs succeed, 1 if any fail.
    """
    import concurrent.futures
    import threading
    import time

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal = {"succeeded", "failed", "cancelled", "timed_out"}
    print_lock = threading.Lock()
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
        url = f"{base}/v1/builds/{job_id}/log/stream"
        try:
            with httpx.Client(timeout=None) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code >= 400:
                        with print_lock:
                            click.echo(f"  [{label}] log stream unavailable ({resp.status_code})")
                        return
                    for line in resp.iter_lines():
                        if line.startswith("data: "):
                            with print_lock:
                                click.echo(f"[{label}] {line[6:]}")
                        elif line.startswith("event: done"):
                            break
        except Exception:
            pass  # best-effort

    def _poll_and_spawn(executor: concurrent.futures.ThreadPoolExecutor):
        """Poll for new jobs in the DAG and spawn followers."""
        # Use prefix matching: append '*' so the server returns all
        # DAGs whose ID starts with the given string.
        query_dag = dag_id if "-" in dag_id and dag_id.count("-") >= 5 else f"{dag_id}*"
        while True:
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

            all_terminal = True
            for j in jobs:
                jid = j["id"]
                status = j.get("status", "unknown")
                label = _make_label(j)

                if status in terminal:
                    if jid not in final_statuses:
                        final_statuses[jid] = status
                        icon = "\u2713" if status == "succeeded" else "\u2717"
                        with print_lock:
                            click.echo(f"  {icon} #{jid} {label}: {status}")
                else:
                    all_terminal = False

                # Start following active (non-pending) jobs we haven't seen
                if jid not in seen_jobs and status not in ("pending", *terminal):
                    seen_jobs.add(jid)
                    with print_lock:
                        click.echo(f"  \u25b6 #{jid} {label}: streaming log...")
                    executor.submit(_follow_job, jid, label)

            if all_terminal and jobs:
                break
            time.sleep(5)

    click.echo(f"Following DAG: {dag_id}")
    click.echo("Waiting for jobs to appear...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
        _poll_and_spawn(executor)

    # Summary
    succeeded = sum(1 for s in final_statuses.values() if s == "succeeded")
    failed = sum(1 for s in final_statuses.values() if s != "succeeded")
    total = len(final_statuses)
    click.echo(f"\nDAG {dag_id}: {succeeded}/{total} succeeded, {failed} failed")

    if failed:
        raise SystemExit(1)


# ── Build-wait helpers ──────────────────────────────────────────────


def _wait_for_jobs(server: str, token: str, job_ids: list[int]) -> None:
    """Poll until all job IDs reach a terminal state, printing updates."""
    import time

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal = {"succeeded", "failed", "cancelled", "timed_out"}
    pending = set(job_ids)

    click.echo(f"\nWaiting for {len(pending)} job(s)...")
    with httpx.Client(timeout=30) as client:
        while pending:
            time.sleep(5)
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


def _wait_for_dags(server: str, token: str, dag_ids: list[str]) -> None:
    """Poll until all jobs in the given DAGs reach terminal state."""
    import time

    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    terminal = {"succeeded", "failed", "cancelled", "timed_out"}

    click.echo(f"\nWaiting for {len(dag_ids)} DAG(s)...")
    all_job_ids: set[int] = set()
    finished: set[int] = set()
    counts: dict[str, int] = {}

    with httpx.Client(timeout=30) as client:
        while True:
            time.sleep(5)
            still_running = False
            for did in dag_ids:
                resp = client.get(
                    f"{base}/v1/builds",
                    headers=headers,
                    params={"dag_id": did, "limit": 1000},
                )
                if resp.status_code >= 400:
                    continue
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
                                f"({j['platform']}/{j.get('arch','?')}): {status}"
                            )
                        counts[status] = counts.get(status, 0) + (1 if jid not in finished else 0)
                    else:
                        still_running = True

            if not still_running and all_job_ids:
                break
            if all_job_ids:
                click.echo(
                    f"  ... {len(all_job_ids) - len(finished)}/{len(all_job_ids)} "
                    f"job(s) remaining",
                    err=True,
                )

    n_failed = sum(1 for jid in all_job_ids if jid not in finished) + len(
        [j for j in all_job_ids if False]  # placeholder
    )
    # Re-check final states
    failed_ids: list[int] = []
    with httpx.Client(timeout=30) as client:
        for jid in all_job_ids:
            resp = client.get(f"{base}/v1/builds/{jid}", headers=headers)
            if resp.status_code < 400:
                info = resp.json()
                if info.get("status") != "succeeded":
                    failed_ids.append(jid)
    if failed_ids:
        raise click.ClickException(f"{len(failed_ids)} job(s) did not succeed: {failed_ids}")
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
@click.option("--arch", required=True, help="Target architecture (e.g. x86_64, aarch64).")
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
        _wait_for_jobs(server, token, [data["id"]])


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
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
    recipe_names: tuple[str, ...],
):
    """Submit a DAG of remote build jobs.

    Provide one or more recipe names as positional arguments.
    Use --config all / --link all to expand the build matrix.
    Dependencies are resolved from recipe.yaml files and jobs are
    ordered so that each recipe builds after its dependencies.

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
        """Extract runtime + build dependency names from a recipe."""
        data = recipe_data.get(name, {})
        deps_block = data.get("depends", {})
        names: list[str] = []
        for key in ("runtime", "build"):
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
        for entry in matrix:
            if entry.get("platform") in (plat, "any"):
                return True
        return False

    # Valid platform→arch pairings.  wasm32 only pairs with wasm/wasi.
    _WASM_ARCHES = {"wasm32"}
    _WASM_PLATFORMS = {"wasm", "wasi"}

    dag_ids: list[str] = []
    for plat in platforms:
        for ar in arches:
            # Skip invalid platform/arch combos
            if ar in _WASM_ARCHES and plat not in _WASM_PLATFORMS:
                continue
            if plat in _WASM_PLATFORMS and ar not in _WASM_ARCHES:
                continue
            for cfg in configs:
                for lnk in links:
                    # wasm/wasi only support static linking.
                    if plat in _WASM_PLATFORMS and lnk != "static":
                        continue
                    # Filter recipes: skip those with no matrix entry
                    eligible = [n for n in recipe_names if _has_platform_entry(n, plat)]
                    skipped = set(recipe_names) - set(eligible)
                    if skipped:
                        click.echo(
                            f"  Skipping {len(skipped)} recipe(s) "
                            f"with no {plat} matrix: {', '.join(sorted(skipped))}"
                        )

                    if not eligible:
                        click.echo(f"  No eligible recipes for {plat}/{ar}/{cfg}/{lnk}")
                        continue

                    # Build name→index mapping for depends_on resolution
                    name_to_idx: dict[str, int] = {name: idx for idx, name in enumerate(eligible)}

                    jobs = []
                    for name in eligible:
                        dep_indices = []
                        for dep_name in _dep_names(name):
                            if dep_name in name_to_idx:
                                dep_indices.append(name_to_idx[dep_name])
                        jobs.append(
                            {
                                "recipe_name": name,
                                "platform": plat,
                                "arch": ar,
                                "config": cfg,
                                "link": lnk,
                                "org_slug": org_slug,
                                "depends_on": dep_indices,
                            }
                        )

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
                        f"DAG {data['dag_id']}: {data['total']} jobs " f"({plat}/{ar}/{cfg}/{lnk})"
                    )

    if wait:
        _wait_for_dags(server, token, dag_ids)


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
    import time

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
            f"Jobs: {len(active)} active, {succeeded} succeeded, "
            f"{failed} failed, {len(jobs)} total"
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
