"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._server import _api_request

# ── Builder commands ────────────────────────────────────────────


@cli.group("builder")
def builder_group() -> None:
    """Manage remote build agents."""


@builder_group.command("list")
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
@click.option("--platform", default=None, help="Filter by platform.")
@click.option("--arch", default=None, help="Filter by architecture.")
@click.option("--status", default=None, help="Filter by status (online/offline/busy).")
def builder_list(
    server: str, token: str, platform: str | None, arch: str | None, status: str | None
):
    """List registered builders."""
    import httpx

    params: dict[str, str] = {}
    if platform:
        params["platform"] = platform
    if arch:
        params["arch"] = arch
    if status:
        params["status"] = status
    url = f"{server.rstrip('/')}/v1/builders"
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
    builders = data.get("builders", [])
    if not builders:
        click.echo("No builders registered.")
        return
    click.echo(f"{'ID':>5}  {'Name':<24} {'Platform':<10} {'Arch':<10} {'Status':<8} {'Jobs':>4}")
    click.echo("-" * 72)
    for b in builders:
        click.echo(
            f"{b['id']:>5}  {b['name']:<24} {b['platform']:<10} {b['arch']:<10} "
            f"{b['status']:<8} {b['current_jobs']}/{b['max_jobs']:>3}"
        )


@builder_group.command("status")
@click.argument("builder_id", type=int)
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
def builder_status(builder_id: int, server: str, token: str):
    """Show details for a specific builder."""
    data = _api_request("get", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{data['id']}: {data['name']}")
    click.echo(f"  Org:         {data.get('org_slug') or '(global)'}")
    click.echo(f"  Platform:    {data['platform']}/{data['arch']}")
    click.echo(f"  Status:      {data['status']}")
    click.echo(f"  Jobs:        {data['current_jobs']}/{data['max_jobs']}")
    click.echo(f"  Labels:      {', '.join(data.get('labels', [])) or '(none)'}")
    cross = data.get("capabilities", {}).get("cross_platforms", [])
    if cross:
        if cross and isinstance(cross[0], dict):
            cross_strs = [f"{e['platform']}/{e['arch']}" for e in cross]
        else:
            cross_strs = cross
        click.echo(f"  Cross:       {', '.join(cross_strs)}")
    click.echo(f"  Affinity:    {'yes' if data.get('prefer_affinity') else 'no'}")
    click.echo(f"  Last HB:     {data.get('last_heartbeat') or 'never'}")
    click.echo(f"  Registered:  {data.get('created_at', 'unknown')}")


@builder_group.command("run")
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
@click.option("--name", required=True, help="Builder name (unique per org).")
@click.option("--platform", default=None, help="Platform (default: auto-detect).")
@click.option("--arch", default=None, help="Architecture (default: auto-detect).")
@click.option("--org", "org_slug", default="", help="Organization scope.")
@click.option("--max-jobs", type=int, default=1, help="Max concurrent jobs.")
@click.option("--label", "labels", multiple=True, help="Labels (repeatable).")
@click.option(
    "--work-dir",
    type=click.Path(),
    default=None,
    help="Directory for build work trees (default: system temp).",
)
@click.option(
    "--recipe-cache-dir",
    type=click.Path(),
    default=None,
    help="Directory to cache downloaded recipe bundles.",
)
@click.option(
    "--no-websocket",
    is_flag=True,
    default=False,
    help="Disable WebSocket and use HTTP long-poll only.",
)
@click.option(
    "--daemon",
    is_flag=True,
    help="Run as a background daemon (fork and detach).",
)
@click.option(
    "--pidfile",
    type=click.Path(),
    default="",
    help="Path to PID file.  [default: <work-dir>/cvcpkg-builder.pid]",
)
@click.option(
    "--cross-platform",
    "cross_platforms",
    multiple=True,
    help="Cross-compilation target platform (repeatable, e.g. --cross-platform wasm).",
)
@click.option(
    "--cross-arch",
    "cross_archs",
    multiple=True,
    help="Architecture for each --cross-platform (positional pairing). "
    "Defaults: wasm→wasm32, wasi→wasm32, others→host arch.",
)
def builder_run(
    server: str,
    token: str,
    name: str,
    platform: str | None,
    arch: str | None,
    org_slug: str,
    max_jobs: int,
    labels: tuple[str, ...],
    work_dir: str | None,
    recipe_cache_dir: str | None,
    no_websocket: bool,
    daemon: bool,
    pidfile: str,
    cross_platforms: tuple[str, ...],
    cross_archs: tuple[str, ...],
):
    """Register as a builder, poll for jobs, and execute builds.

    Registers this machine as a remote builder, then enters a loop
    that polls the server for dispatched jobs.  For each job the
    builder:

      1. Claims the job
      2. Downloads the recipe bundle (cached locally)
      3. Runs the build via ``pack_recipe()``
      4. Streams build logs back to the server
      5. Publishes the resulting archive
      6. Reports success or failure

    Press Ctrl-C to finish in-flight jobs, unregister, and exit.
    """
    import json
    import shutil
    import signal
    import tarfile
    import tempfile
    import threading
    import time
    import traceback

    import httpx

    from cvcpkg.builder import pack_recipe
    from cvcpkg.platform import detect_arch

    if platform is None:
        import sysconfig

        platform = sysconfig.get_platform().split("-")[0]
    if arch is None:
        arch = detect_arch()

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    work_root = Path(work_dir) if work_dir else None
    cache_dir = (
        Path(recipe_cache_dir)
        if recipe_cache_dir
        else Path(tempfile.gettempdir()) / "cvcpkg-recipe-cache"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Daemonize ───────────────────────────────────────────
    import os as _os

    pid_path = (
        Path(pidfile)
        if pidfile
        else (work_root or Path(tempfile.gettempdir())) / "cvcpkg-builder.pid"
    )

    if daemon:
        import sys as _sys

        if _sys.platform == "win32":
            raise click.ClickException("--daemon is not supported on Windows.")

        click.echo(f"cvcpkg-builder: daemonizing (pidfile {pid_path})...")
        if _os.fork() > 0:
            raise SystemExit(0)
        _os.setsid()
        if _os.fork() > 0:
            raise SystemExit(0)
        devnull = _os.open(_os.devnull, _os.O_RDWR)
        _os.dup2(devnull, _sys.stdin.fileno())
        _os.dup2(devnull, _sys.stdout.fileno())
        _os.dup2(devnull, _sys.stderr.fileno())
        _os.close(devnull)

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(_os.getpid()))

    # ── Build cross-platform/arch pairs ─────────────────────
    _CROSS_ARCH_DEFAULTS = {
        "wasm": "wasm32",
        "wasi": "wasm32",
    }
    cross_entries: list[dict[str, str]] = []
    for i, cp in enumerate(cross_platforms):
        if i < len(cross_archs):
            ca = cross_archs[i]
        else:
            ca = _CROSS_ARCH_DEFAULTS.get(cp, arch or "x86_64")
        cross_entries.append({"platform": cp, "arch": ca})

    # ── Registration ────────────────────────────────────────
    caps: dict = {}
    if cross_entries:
        caps["cross_platforms"] = cross_entries
    body = {
        "name": name,
        "platform": platform,
        "arch": arch,
        "org_slug": org_slug,
        "max_jobs": max_jobs,
        "labels": list(labels),
        "capabilities": caps,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{base}/v1/builders/register", headers=headers, json=body)
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"registration failed ({resp.status_code}): {detail}")
    info = resp.json()
    builder_id = info["id"]
    if cross_entries:
        cross_msg = " [cross: {}]".format(
            ", ".join(f"{e['platform']}/{e['arch']}" for e in cross_entries)
        )
    else:
        cross_msg = ""
    click.echo(f"Registered builder #{builder_id} ({name}) — {platform}/{arch}{cross_msg}")

    shutdown = False
    current_jobs = 0
    jobs_lock = threading.Lock()

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True
        click.echo("\nShutdown requested — finishing in-flight jobs…")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Helpers ─────────────────────────────────────────────

    def _heartbeat():
        """Send heartbeat to server."""
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{base}/v1/builders/{builder_id}/heartbeat",
                    headers=headers,
                    json={"status": "online", "current_jobs": current_jobs},
                )
            if resp.status_code >= 400:
                click.echo(f"  heartbeat failed: {resp.status_code}", err=True)
        except Exception as exc:
            click.echo(f"  heartbeat error: {exc}", err=True)

    def _fetch_recipe(recipe_name: str) -> Path:
        """Download recipe bundle and extract to a local directory.

        Returns the path to the extracted recipe directory.  Bundles
        are cached in *cache_dir* so repeated builds of the same
        recipe don't re-download.
        """
        bundle_path = cache_dir / f"{recipe_name}.tar.gz"

        # Always re-download (server may have a newer version).
        # A future optimisation can compare recipe_hash.
        url = f"{base}/v1/recipes/{recipe_name}"
        params: dict[str, str] = {}
        if org_slug:
            params["org_slug"] = org_slug
        with httpx.Client(timeout=120) as client:
            resp = client.get(url, headers=headers, params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"failed to download recipe '{recipe_name}': {resp.status_code}")
        bundle_path.write_bytes(resp.content)

        # Extract
        extract_dir = cache_dir / recipe_name
        if extract_dir.is_dir():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(path=extract_dir)  # noqa: S202

        # recipe_push stores recipe files under ``<name>/`` inside
        # the tar (with ``_common/`` alongside).  If that nested dir
        # exists, return it so that ``../_common`` resolves correctly
        # from build scripts.  Fall back to the flat layout for
        # bundles created before this convention.
        nested = extract_dir / recipe_name
        if nested.is_dir() and (nested / "recipe.yaml").is_file():
            return nested
        return extract_dir

    # Shared HTTP client for log streaming (created once, avoids
    # connection overhead on every chunk).
    _log_client = httpx.Client(timeout=30)

    def _stream_log(job_id: int, text: str):
        """Append a chunk of build log to the server."""
        # Truncate to 64 KB per-chunk (server limit)
        for i in range(0, len(text), 65536):
            chunk = text[i : i + 65536]
            try:
                _log_client.patch(
                    f"{base}/v1/builds/{job_id}/log",
                    headers=headers,
                    json={"data": chunk},
                )
            except Exception:
                pass  # best-effort log streaming

    def _execute_job(job: dict) -> None:
        """Execute a single build job."""
        nonlocal current_jobs
        job_id = job["id"]
        recipe_name = job["recipe_name"]
        job_platform = job.get("platform", platform)
        job_arch = job.get("arch", arch)
        job_config = job.get("config", "release")
        job_link = job.get("link", "shared")

        click.echo(
            f"  [{job_id}] Building {recipe_name} "
            f"({job_platform}/{job_arch}/{job_config}/{job_link})"
        )

        # 1. Claim the job
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{base}/v1/builds/{job_id}/claim",
                    headers=headers,
                    json={"builder_id": builder_id},
                )
            if resp.status_code >= 400:
                click.echo(
                    f"  [{job_id}] claim failed ({resp.status_code}), skipping",
                    err=True,
                )
                return
        except Exception as exc:
            click.echo(f"  [{job_id}] claim error: {exc}", err=True)
            return

        error_message = ""
        archive_path: Path | None = None
        try:
            # 2. Download recipe
            _stream_log(job_id, f"Downloading recipe '{recipe_name}'…\n")
            recipe_dir = _fetch_recipe(recipe_name)
            _stream_log(job_id, f"Recipe extracted to {recipe_dir}\n")

            # 3. Build + package
            # Detect cross-compilation: job targets a different platform
            # than the builder's native platform (e.g. wasm on linux).
            host_plat = ""
            if job_platform != platform:
                host_plat = platform
                _stream_log(
                    job_id,
                    f"Cross-compiling: target={job_platform}, host={host_plat}\n",
                )

            _stream_log(
                job_id,
                f"Starting build: {recipe_name} "
                f"({job_platform}/{job_arch}/{job_config}/{job_link})\n",
            )

            output_dir = Path(tempfile.mkdtemp(prefix=f"cvcpkg-out-{recipe_name}-"))
            try:
                archive_path, sha256, size = pack_recipe(
                    recipe_dir,
                    platform=job_platform,
                    arch=job_arch,
                    config=job_config,
                    link=job_link,
                    output_dir=output_dir,
                    work_dir_root=work_root,
                    log_callback=lambda text, _jid=job_id: _stream_log(_jid, text),
                    host_platform=host_plat,
                )
                _stream_log(
                    job_id,
                    f"Build succeeded: {archive_path.name} " f"({size:,} bytes, sha256={sha256})\n",
                )
            except Exception as exc:
                error_message = f"build failed: {exc}\n{traceback.format_exc()}"
                _stream_log(job_id, error_message)
                raise

            # 4. Publish the archive to the server
            _stream_log(job_id, f"Publishing {archive_path.name}…\n")
            _publish_to_server(
                server=base,
                token=token,
                archive_paths=[archive_path],
                release_tag="",
                chunked_threshold=10 * 1024 * 1024,
                org=org_slug,
            )
            result_url = f"{base}/v1/packages/{recipe_name}"
            _stream_log(job_id, "Published successfully.\n")

            # 5. Report completion
            with httpx.Client(timeout=30) as client:
                client.post(
                    f"{base}/v1/builds/{job_id}/complete",
                    headers=headers,
                    json={"result_archive_url": result_url},
                )
            click.echo(f"  [{job_id}] Completed: {recipe_name}")

        except Exception as exc:
            # Report failure
            if not error_message:
                error_message = f"{exc}\n{traceback.format_exc()}"
            try:
                with httpx.Client(timeout=30) as client:
                    client.post(
                        f"{base}/v1/builds/{job_id}/fail",
                        headers=headers,
                        json={"error_message": error_message[:4096]},
                    )
            except Exception:
                pass
            click.echo(f"  [{job_id}] Failed: {recipe_name} — {exc}", err=True)

        finally:
            # Clean up output dir
            if archive_path and archive_path.parent.is_dir():
                shutil.rmtree(archive_path.parent, ignore_errors=True)
            with jobs_lock:
                current_jobs -= 1

    # ── WebSocket helpers ───────────────────────────────────

    def _ws_url() -> str:
        """Build WebSocket URL from the HTTP base URL."""
        scheme = "wss" if base.startswith("https") else "ws"
        rest = base.split("://", 1)[1] if "://" in base else base
        return f"{scheme}://{rest}/v1/builders/{builder_id}/ws?token={token}"

    def _run_ws_loop():
        """Run the WebSocket event loop.

        Connects to the server, sends heartbeats, receives
        dispatched jobs and recipe pushes.  Falls back to HTTP
        long-poll on any connection failure.
        """
        nonlocal shutdown, current_jobs, last_heartbeat
        try:
            import websockets.sync.client as ws_sync
        except ImportError:
            click.echo("  websockets not installed — using HTTP long-poll", err=True)
            return False

        click.echo("Connecting via WebSocket…")
        try:
            with ws_sync.connect(_ws_url(), close_timeout=5) as ws:
                click.echo("WebSocket connected.")
                ws.settimeout(5)  # non-blocking reads with 5s timeout
                while not shutdown:
                    # Send heartbeat if due
                    now = time.time()
                    if now - last_heartbeat >= heartbeat_interval:
                        try:
                            ws.send(
                                json.dumps(
                                    {
                                        "type": "heartbeat",
                                        "status": "online",
                                        "current_jobs": current_jobs,
                                    }
                                )
                            )
                            last_heartbeat = now
                        except Exception:
                            break  # connection lost

                    # Try to receive a message
                    try:
                        raw = ws.recv(timeout=2)
                    except TimeoutError:
                        continue
                    except Exception:
                        break  # connection lost

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    msg_type = msg.get("type", "")

                    if msg_type == "job.dispatch":
                        job = msg.get("job")
                        if job is None:
                            continue
                        with jobs_lock:
                            if current_jobs >= max_jobs:
                                continue
                            current_jobs += 1
                        t = threading.Thread(target=_execute_job, args=(job,), daemon=True)
                        t.start()

                    elif msg_type == "recipe.push":
                        recipe = msg.get("recipe", {})
                        rname = recipe.get("name", "")
                        if rname:
                            click.echo(f"  Recipe updated: {rname}")
                            try:
                                _fetch_recipe(rname)
                            except Exception:
                                pass

                    elif msg_type == "ping":
                        try:
                            ws.send(json.dumps({"type": "pong"}))
                        except Exception:
                            break

                    elif msg_type == "job.timeout":
                        job_id = msg.get("job_id")
                        click.echo(
                            f"  [{job_id}] Server timed out job",
                            err=True,
                        )

            return True  # ran successfully (normal shutdown)

        except Exception as exc:
            click.echo(
                f"  WebSocket connection failed: {exc} — " f"falling back to HTTP long-poll",
                err=True,
            )
            return False

    # ── Main loop ───────────────────────────────────────────

    last_heartbeat = 0.0
    heartbeat_interval = 60.0
    poll_interval = 5.0  # seconds between next-job polls

    try:
        # Try WebSocket first (unless disabled)
        use_ws = not no_websocket
        if use_ws and not shutdown:
            ws_ok = _run_ws_loop()
            if ws_ok:
                # WebSocket ran until shutdown — skip HTTP loop
                use_ws = True
            else:
                use_ws = False

        # HTTP long-poll fallback
        while not shutdown:
            # Heartbeat
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                _heartbeat()
                last_heartbeat = now

            # Check capacity
            with jobs_lock:
                available = max_jobs - current_jobs
            if available <= 0:
                time.sleep(poll_interval)
                continue

            # Poll for next job (short timeout so we stay responsive)
            try:
                with httpx.Client(timeout=35) as client:
                    resp = client.get(
                        f"{base}/v1/builders/{builder_id}/next-job",
                        headers=headers,
                        params={"timeout": "5"},
                    )
            except Exception as exc:
                click.echo(f"  poll error: {exc}", err=True)
                time.sleep(poll_interval)
                continue

            if resp.status_code == 204:
                # No job available
                continue
            if resp.status_code >= 400:
                click.echo(
                    f"  poll failed: {resp.status_code}",
                    err=True,
                )
                time.sleep(poll_interval)
                continue

            job = resp.json()
            with jobs_lock:
                current_jobs += 1

            # Run in a thread so we can keep heartbeating & polling
            t = threading.Thread(target=_execute_job, args=(job,), daemon=True)
            t.start()

    finally:
        # Wait for in-flight jobs
        deadline = time.time() + 300  # 5 min grace period
        while current_jobs > 0 and time.time() < deadline:
            click.echo(f"  Waiting for {current_jobs} in-flight job(s)…")
            time.sleep(5)

        click.echo("Shutting down — unregistering builder…")
        try:
            with httpx.Client(timeout=10) as client:
                client.delete(f"{base}/v1/builders/{builder_id}", headers=headers)
            click.echo("Builder unregistered.")
        except Exception:
            click.echo("Warning: failed to unregister builder.", err=True)
        finally:
            try:
                pid_path.unlink(missing_ok=True)
            except OSError:
                pass


@builder_group.command("stop")
@click.argument("builder_id", type=int)
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
    help="Bearer token (admin).  [env: CVCPKG_TOKEN]",
)
def builder_stop(builder_id: int, server: str, token: str):
    """Unregister a builder by ID (admin-only)."""
    _api_request("delete", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{builder_id} unregistered.")


