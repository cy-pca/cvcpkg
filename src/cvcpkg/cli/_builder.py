"""CLI commands - auto-extracted from cli.py."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import click

from cvcpkg._archive import safe_tar_extractall
from cvcpkg.cli import cli
from cvcpkg.cli._publish import _publish_to_server
from cvcpkg.cli._server import _api_request

# Exit code the builder uses to ask its supervisor wrapper to pull the latest
# cvcpkg and relaunch it (Windows supervised self-update).  Kept in sync with
# windows/cvcpkg-builder-supervisor.cmd in the vm-provisioning repo.
_SUPERVISOR_RESTART_CODE = 90

# -- Builder commands --------------------------------------------


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
    "--exit-when-empty",
    is_flag=True,
    default=False,
    help="Drain mode: exit 0 once the queue has no claimable job and none are "
    "in flight.  For ephemeral/CI runners.  Forces HTTP long-poll (the "
    "WebSocket path has no empty-queue signal).",
)
@click.option(
    "--max-runtime",
    type=float,
    default=None,
    help="Wall-clock budget in seconds.  Stop claiming new jobs once exceeded, "
    "let in-flight jobs finish, then exit 0.  For time-boxed CI runners that "
    "must stay under a hard job timeout.",
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
    "Defaults: wasm->wasm32, wasi->wasm32, others->host arch.",
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
    exit_when_empty: bool,
    max_runtime: float | None,
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
    import shutil
    import signal
    import tarfile
    import tempfile
    import threading
    import traceback
    import zipfile

    import httpx

    from cvcpkg.builder import pack_recipe
    from cvcpkg.platform import detect_arch, detect_platform

    if platform is None:
        platform = detect_platform()
    if arch is None:
        arch = detect_arch()

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    work_root = Path(work_dir) if work_dir else None
    if work_root is not None:
        # Create the work-dir root up front (mirrors cache_dir below).  On a
        # long-lived builder a /tmp reaper (systemd-tmpfiles, BSD /etc/periodic
        # daily clean, tmpwatch) can later delete it out from under us; each job
        # re-ensures it before mkdtemp (see _execute_job).
        work_root.mkdir(parents=True, exist_ok=True)
    cache_dir = (
        Path(recipe_cache_dir)
        if recipe_cache_dir
        else Path(tempfile.gettempdir()) / "cvcpkg-recipe-cache"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Per-recipe locks serialize concurrent _fetch_recipe() calls on the same
    # name.  Without this, the main job thread and the recipe.push websocket
    # handler thread can both rmtree + mkdir + extractall the same directory,
    # producing "recipe.yaml not found" mid-extraction.
    _recipe_fetch_locks: dict[str, threading.Lock] = {}
    _recipe_fetch_locks_guard = threading.Lock()

    def _get_recipe_lock(name: str) -> threading.Lock:
        with _recipe_fetch_locks_guard:
            return _recipe_fetch_locks.setdefault(name, threading.Lock())

    # -- Daemonize -------------------------------------------
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

    # -- Single-instance guard -------------------------------
    # The builder must be a singleton per host.  A second concurrent
    # ``cvcpkg builder run`` would register a duplicate builder, race on the
    # shared work / recipe-cache directories, and (historically) pile up as
    # "a ton of cvcpkg processes".  If the pidfile names a still-live builder,
    # refuse to start; a stale pidfile (dead PID, or PID recycled by an
    # unrelated program) is silently reclaimed.
    def _pid_is_live_builder(pid: int) -> bool:
        if pid <= 0 or pid == _os.getpid():
            return False
        if sys.platform == "win32":
            import subprocess as _sp

            try:
                out = _sp.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout.lower()
            except Exception:
                return False
            # Only a live python/cvcpkg image counts - otherwise the PID was
            # recycled by an unrelated process and the pidfile is stale.
            # NB: never use os.kill(pid, 0) here; on Windows signal 0 maps to
            # TerminateProcess and would *kill* the process being probed.
            return f'"{pid}"' in out and ("python" in out or "cvcpkg" in out)
        try:
            _os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            return True
        return True

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    if pid_path.exists():
        try:
            _existing_pid = int(pid_path.read_text().strip() or "0")
        except ValueError:
            _existing_pid = 0
        if _pid_is_live_builder(_existing_pid):
            raise click.ClickException(
                f"another cvcpkg builder is already running (pid {_existing_pid}, "
                f"pidfile {pid_path}); refusing to start a second instance. "
                f"Stop it first, or delete the pidfile if it is stale."
            )
        pid_path.unlink(missing_ok=True)  # stale - reclaim it
    pid_path.write_text(str(_os.getpid()))

    # -- Build cross-platform/arch pairs ---------------------
    _cross_arch_defaults = {
        "wasm": "wasm32",
        "wasi": "wasm32",
    }
    cross_entries: list[dict[str, str]] = []
    for i, cp in enumerate(cross_platforms):
        if i < len(cross_archs):
            ca = cross_archs[i]
        else:
            ca = _cross_arch_defaults.get(cp, arch or "x86_64")
        cross_entries.append({"platform": cp, "arch": ca})

    # -- Registration ----------------------------------------
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
    click.echo(f"Registered builder #{builder_id} ({name}) - {platform}/{arch}{cross_msg}")

    shutdown = False
    # ``current_jobs`` is derived from the set of in-flight job tokens so it
    # can never desync: a token is added under the lock when a job thread is
    # launched and removed in that thread's finally (see _run_job_guarded),
    # which runs no matter how the job exits.  A raw increment/decrement
    # counter previously leaked a slot whenever the claim step returned early,
    # wedging the builder at max capacity forever.
    active_jobs: set[int] = set()
    _job_seq = 0
    current_jobs = 0
    jobs_lock = threading.Lock()

    def _claim_slot() -> int:
        """Reserve a slot; returns a unique token to release it with.
        Caller must hold no assumption beyond passing the token to
        _release_slot.  Call under jobs_lock."""
        nonlocal _job_seq, current_jobs
        _job_seq += 1
        active_jobs.add(_job_seq)
        current_jobs = len(active_jobs)
        return _job_seq

    def _release_slot(token: int) -> None:
        nonlocal current_jobs
        with jobs_lock:
            active_jobs.discard(token)
            current_jobs = len(active_jobs)

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True
        click.echo("\nShutdown requested - finishing in-flight jobs...")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # -- Helpers ---------------------------------------------

    def _heartbeat():
        """Send heartbeat to server."""
        with jobs_lock:
            jobs_now = current_jobs
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{base}/v1/builders/{builder_id}/heartbeat",
                    headers=headers,
                    json={"status": "online", "current_jobs": jobs_now},
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
        # Serialize per recipe name - the job-execution thread and the
        # recipe.push websocket handler thread can otherwise concurrently
        # rmtree+mkdir+extractall the same directory, and Recipe.load()
        # then observes a mid-extraction state ("recipe.yaml not found").
        with _get_recipe_lock(recipe_name):
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
            # cache_dir may have been reaped (e.g. OpenBSD /tmp cleanup) between
            # builder startup and this call; recreate before writing.
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_bytes(resp.content)

            # Extract
            extract_dir = cache_dir / recipe_name
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            if extract_dir.exists():
                # rmtree left remnants - force remove
                import subprocess

                subprocess.run(["rm", "-rf", str(extract_dir)], check=False)
            extract_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(bundle_path, "r:gz") as tar:
                safe_tar_extractall(tar, extract_dir)

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

    def _extract_dep_names(
        recipe_dir: Path,
        job_platform: str,
    ) -> list[str]:
        """Return direct dependency names from a recipe directory."""
        import yaml as _yaml

        recipe_yaml = recipe_dir / "recipe.yaml"
        if not recipe_yaml.is_file():
            return []
        data = _yaml.safe_load(recipe_yaml.read_text())
        deps_block = data.get("depends", {})

        names: list[str] = []
        for key in ("runtime", "build", "host_tools"):
            for dep in deps_block.get(key, []) or []:
                if isinstance(dep, str):
                    names.append(dep)
                elif isinstance(dep, dict):
                    plats = dep.get("platforms")
                    if plats and job_platform not in plats:
                        continue
                    names.append(dep["name"])
        return names

    def _resolve_transitive_deps(
        recipe_dir: Path,
        job_platform: str,
        log_cb: Callable[[str], None],
    ) -> list[str]:
        """Compute the full transitive closure of dependencies.

        Returns dep names in topological order (deepest deps first)
        so that when packages are extracted into the prefix, transitive
        libraries are available before the packages that need them.
        """
        direct = _extract_dep_names(recipe_dir, job_platform)
        if not direct:
            return []

        # BFS to collect all transitive deps
        visited: set[str] = set()
        order: list[str] = []
        queue = list(direct)
        while queue:
            name = queue.pop(0)
            if name in visited:
                continue
            visited.add(name)
            # Fetch this dep's recipe to find *its* deps
            try:
                dep_recipe_dir = _fetch_recipe(name)
                sub_deps = _extract_dep_names(dep_recipe_dir, job_platform)
                for sd in sub_deps:
                    if sd not in visited:
                        queue.append(sd)
            except Exception:
                # Recipe fetch may fail for host-tools that aren't
                # packaged as recipes (system cmake, etc.) - skip.
                pass
            order.append(name)
        return order

    def _install_deps(
        recipe_dir: Path,
        prefix: Path,
        job_platform: str,
        job_arch: str,
        job_config: str,
        job_link: str,
        log_cb: Callable[[str], None],
    ) -> None:
        """Download and install runtime dependencies into *prefix*.

        Resolves the full transitive dependency closure, then queries
        the server catalog for each dep, downloads the matching
        archive, and extracts it into the shared prefix so that
        dependent builds can find headers/libraries.
        """
        dep_names = _resolve_transitive_deps(recipe_dir, job_platform, log_cb)
        if not dep_names:
            return

        prefix.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=120) as client:
            for dep_name in dep_names:
                # Find the package on the server
                resp = client.get(
                    f"{base}/v1/packages/{dep_name}",
                    headers=headers,
                )
                if resp.status_code >= 400:
                    log_cb(f"  dep {dep_name}: not found on server (skipping)\n")
                    continue

                pkgs = resp.json().get("packages", [])
                # Find best match for platform/arch/config/link
                match = None
                for p in pkgs:
                    if (
                        p.get("platform") == job_platform
                        and p.get("arch") == job_arch
                        and p.get("build_type", "release") == job_config
                        and p.get("link", "shared") == job_link
                    ):
                        match = p
                        break
                # Relax: try just platform/arch
                if match is None:
                    for p in pkgs:
                        if p.get("platform") == job_platform and p.get("arch") == job_arch:
                            match = p
                            break
                if match is None:
                    log_cb(
                        f"  dep {dep_name}: no matching variant for "
                        f"{job_platform}/{job_arch} (skipping)\n"
                    )
                    continue

                archive_url = match.get("archive_url", "")
                if not archive_url:
                    log_cb(f"  dep {dep_name}: no archive URL (skipping)\n")
                    continue

                # Ensure absolute URL (archive_url is a relative path like /v1/download/...)
                if archive_url.startswith("/"):
                    archive_url = f"{base}{archive_url}"

                # Download the archive
                log_cb(f"  Installing dep: {dep_name} ({match.get('version', '')})\n")
                dl_resp = client.get(archive_url)
                if dl_resp.status_code >= 400:
                    log_cb(f"  dep {dep_name}: download failed ({dl_resp.status_code})\n")
                    continue

                # Extract into prefix.  The catalog's archive_url suffix
                # (typically .tar.zst) is purely cosmetic - the server
                # serves whatever the builder produced (Linux/BSD/macOS:
                # gzip; Windows: zip).  Sniff the magic bytes instead.
                archive_bytes = dl_resp.content
                head = archive_bytes[:4]
                if head[:2] == b"PK":
                    suffix, kind = ".zip", "zip"
                elif head[:2] == b"\x1f\x8b":
                    suffix, kind = ".tar.gz", "gz"
                elif head == b"\x28\xb5\x2f\xfd":
                    suffix, kind = ".tar.zst", "zst"
                else:
                    suffix, kind = ".bin", "unknown"
                tmp_archive = prefix / f"_dep_{dep_name}{suffix}"
                tmp_archive.write_bytes(archive_bytes)
                try:
                    if kind == "zip":
                        with zipfile.ZipFile(tmp_archive) as zf:
                            zf.extractall(path=prefix)  # noqa: S202
                    elif kind == "gz":
                        with tarfile.open(tmp_archive, "r:gz") as tf:
                            tf.extractall(path=prefix)  # noqa: S202
                    elif kind == "zst":
                        import zstandard  # type: ignore[import-untyped]

                        with open(tmp_archive, "rb") as f_in:
                            dctx = zstandard.ZstdDecompressor()
                            with dctx.stream_reader(f_in) as reader:
                                with tarfile.open(fileobj=reader, mode="r|") as tf:
                                    tf.extractall(path=prefix)  # noqa: S202
                    else:
                        raise ValueError(f"unknown archive format (magic={head!r})")
                except Exception as exc:
                    log_cb(f"  dep {dep_name}: extract failed ({exc})\n")
                finally:
                    tmp_archive.unlink(missing_ok=True)

                # Fix up pkg-config .pc files: replace hardcoded build-time
                # prefix with the actual install prefix so that downstream
                # configure / cmake find_package calls work correctly.
                pc_dir = prefix / "lib" / "pkgconfig"
                if pc_dir.is_dir():
                    prefix_str = str(prefix.resolve())
                    for pc_file in pc_dir.glob("*.pc"):
                        text = pc_file.read_text()
                        # Use a lambda for the replacement so that backslashes
                        # in Windows paths (e.g. C:\Users\...) are not treated
                        # as regex group references / unknown escapes.
                        fixed = re.sub(
                            r"^prefix=.*$",
                            lambda _m, p=prefix_str: f"prefix={p}",
                            text,
                            count=1,
                            flags=re.MULTILINE,
                        )
                        if fixed != text:
                            pc_file.write_text(fixed)

    def _install_cross_toolchains(
        target_platform: str,
        host_platform: str,
        host_arch: str,
        prefix: Path,
        log_cb: Callable[[str], None],
        cache_dir: Path | None = None,
    ) -> dict[str, str]:
        """Install cross-toolchain packages and return their env vars.

        Queries the server for recipes that provide a cross-toolchain
        for *target_platform* (e.g. emsdk for wasm, wasi-sdk for wasi).
        Downloads the pre-built host-platform package and extracts it.
        Returns a merged ``cross_toolchain_env`` dict with ``${PREFIX}``
        already resolved to the actual *prefix* path.

        When *cache_dir* is set, toolchain archives are extracted there
        once and symlinked into *prefix* on subsequent calls, avoiding
        repeated downloads of large toolchains (~800 MB for emsdk).
        """
        import yaml as _yaml

        # Map target platforms -> known toolchain recipe names.
        # The builder fetches the recipe bundle to read cross_toolchain.env
        # dynamically, but needs to know which recipes to look for.
        _toolchain_map: dict[str, list[str]] = {
            "wasm": ["emsdk"],
            "wasi": ["wasi-sdk"],
            "cosmo": ["cosmocc"],
        }
        toolchain_names = _toolchain_map.get(target_platform, [])
        if not toolchain_names:
            return {}

        merged_env: dict[str, str] = {}
        prefix.mkdir(parents=True, exist_ok=True)

        for tc_name in toolchain_names:
            # 1. Fetch the toolchain recipe bundle to read cross_toolchain.env
            try:
                tc_recipe_dir = _fetch_recipe(tc_name)
            except Exception as exc:
                log_cb(f"  toolchain {tc_name}: recipe fetch failed ({exc})\n")
                continue

            tc_yaml_path = tc_recipe_dir / "recipe.yaml"
            if not tc_yaml_path.is_file():
                log_cb(f"  toolchain {tc_name}: no recipe.yaml\n")
                continue

            tc_data = _yaml.safe_load(tc_yaml_path.read_text())
            ct_block = tc_data.get("cross_toolchain", {})
            ct_env = ct_block.get("env", {}) or {}
            ct_host_tools = (tc_data.get("depends", {}) or {}).get("host_tools", []) or []

            # 2. Download the pre-built package for the HOST platform
            with httpx.Client(timeout=120) as client:
                resp = client.get(
                    f"{base}/v1/packages/{tc_name}",
                    headers=headers,
                )
                if resp.status_code >= 400:
                    log_cb(f"  toolchain {tc_name}: not found on server ({resp.status_code})\n")
                    continue

                pkgs = resp.json().get("packages", [])
                match = None
                for p in pkgs:
                    if p.get("platform") == host_platform and p.get("arch") == host_arch:
                        match = p
                        break
                if match is None:
                    log_cb(
                        f"  toolchain {tc_name}: no {host_platform}/{host_arch} package on server\n"
                    )
                    continue

                archive_url = match.get("archive_url", "")
                if not archive_url:
                    log_cb(f"  toolchain {tc_name}: no archive URL\n")
                    continue

                # Ensure absolute URL
                if archive_url.startswith("/"):
                    archive_url = f"{base}{archive_url}"

                tc_version = match.get("version", "unknown")

                # -- Persistent toolchain cache ----------------------
                # When a cache_dir is provided, extract the toolchain
                # once into cache_dir/toolchains/<name>-<version>/
                # and symlink its contents into the per-build prefix.
                # This avoids re-downloading ~300-800 MB archives on
                # every cross-compilation job.
                extract_target = prefix
                tc_cache_path: Path | None = None
                if cache_dir is not None:
                    tc_cache_root = cache_dir / "toolchains"
                    tc_cache_path = tc_cache_root / f"{tc_name}-{tc_version}"
                    if tc_cache_path.is_dir() and any(tc_cache_path.iterdir()):
                        log_cb(
                            f"  Toolchain {tc_name} ({tc_version}) cached, symlinking into prefix\n"
                        )
                        # Symlink cached contents into the build prefix
                        for child in tc_cache_path.iterdir():
                            dst = prefix / child.name
                            if not dst.exists():
                                dst.symlink_to(child)
                        # Resolve env and skip download
                        for var, tpl in ct_env.items():
                            merged_env[var] = tpl.replace("${PREFIX}", str(prefix))
                        log_cb(
                            f"  Toolchain {tc_name} ready "
                            f"({', '.join(f'{k}={v}' for k, v in ct_env.items())})\n"
                        )
                        # Still install host_tools (cheap, small packages)
                        for tool_name in ct_host_tools:
                            if isinstance(tool_name, dict):
                                tool_name = tool_name.get("name", "")
                            if not tool_name:
                                continue
                            try:
                                _install_host_package(
                                    tool_name, host_platform, host_arch, prefix, log_cb
                                )
                            except Exception as exc:
                                log_cb(f"  host_tool {tool_name}: install failed ({exc})\n")
                        continue
                    # Not cached yet - extract into cache dir
                    tc_cache_path.mkdir(parents=True, exist_ok=True)
                    extract_target = tc_cache_path

                log_cb(f"  Installing cross-toolchain: {tc_name} ({tc_version})\n")
                dl_resp = client.get(archive_url)
                if dl_resp.status_code >= 400:
                    log_cb(f"  toolchain {tc_name}: download failed ({dl_resp.status_code})\n")
                    continue

                tc_bytes = dl_resp.content
                head = tc_bytes[:4]
                if head[:2] == b"PK":
                    suffix, kind = ".zip", "zip"
                elif head[:2] == b"\x1f\x8b":
                    suffix, kind = ".tar.gz", "gz"
                elif head == b"\x28\xb5\x2f\xfd":
                    suffix, kind = ".tar.zst", "zst"
                else:
                    suffix, kind = ".bin", "unknown"
                tmp_archive = extract_target / f"_toolchain_{tc_name}{suffix}"
                tmp_archive.write_bytes(tc_bytes)
                try:
                    if kind == "zip":
                        with zipfile.ZipFile(tmp_archive) as zf:
                            zf.extractall(path=extract_target)  # noqa: S202
                    elif kind == "gz":
                        with tarfile.open(tmp_archive, "r:gz") as tf:
                            tf.extractall(path=extract_target)  # noqa: S202
                    elif kind == "zst":
                        import zstandard  # type: ignore[import-untyped]

                        with open(tmp_archive, "rb") as f_in:
                            dctx = zstandard.ZstdDecompressor()
                            with dctx.stream_reader(f_in) as reader:
                                with tarfile.open(fileobj=reader, mode="r|") as tf:
                                    tf.extractall(path=extract_target)  # noqa: S202
                    else:
                        raise ValueError(f"unknown archive format (magic={head!r})")
                except Exception as exc:
                    log_cb(f"  toolchain {tc_name}: extract failed ({exc})\n")
                    # Clean up broken cache entry
                    if tc_cache_path and tc_cache_path.is_dir():
                        shutil.rmtree(tc_cache_path, ignore_errors=True)
                    continue
                finally:
                    tmp_archive.unlink(missing_ok=True)

            # If we extracted into the cache, symlink into prefix now
            if tc_cache_path and extract_target != prefix:
                for child in tc_cache_path.iterdir():
                    dst = prefix / child.name
                    if not dst.exists():
                        dst.symlink_to(child)

            # 3. Resolve env templates
            for var, tpl in ct_env.items():
                merged_env[var] = tpl.replace("${PREFIX}", str(prefix))

            log_cb(
                f"  Toolchain {tc_name} installed "
                f"({', '.join(f'{k}={v}' for k, v in ct_env.items())})\n"
            )

            # 4. Install host_tools declared by the toolchain recipe
            # (e.g. wasmtime for wasi-sdk so test scripts can execute
            # wasm32-wasi binaries).  These are fetched as host_platform
            # packages and extracted into the same prefix.
            for tool_name in ct_host_tools:
                if isinstance(tool_name, dict):
                    tool_name = tool_name.get("name", "")
                if not tool_name:
                    continue
                try:
                    _install_host_package(tool_name, host_platform, host_arch, prefix, log_cb)
                except Exception as exc:
                    log_cb(f"  host_tool {tool_name}: install failed ({exc})\n")

        return merged_env

    def _install_host_package(
        pkg_name: str,
        host_platform: str,
        host_arch: str,
        prefix: Path,
        log_cb: Callable[[str], None],
    ) -> None:
        """Fetch a pre-built package for the host platform and extract to *prefix*.

        Used to install cross-toolchain companion tools like wasmtime
        alongside wasi-sdk.  Best-effort: logs and returns on any failure
        so the build can proceed without the tool.
        """
        with httpx.Client(timeout=120) as client:
            resp = client.get(f"{base}/v1/packages/{pkg_name}", headers=headers)
            if resp.status_code >= 400:
                log_cb(f"  host_tool {pkg_name}: not found on server ({resp.status_code})\n")
                return
            pkgs = resp.json().get("packages", [])
            match = None
            for p in pkgs:
                if p.get("platform") == host_platform and p.get("arch") == host_arch:
                    match = p
                    break
            if match is None:
                log_cb(
                    f"  host_tool {pkg_name}: no {host_platform}/{host_arch} package on server\n"
                )
                return

            archive_url = match.get("archive_url", "")
            if not archive_url:
                log_cb(f"  host_tool {pkg_name}: no archive URL\n")
                return
            if archive_url.startswith("/"):
                archive_url = f"{base}{archive_url}"

            log_cb(f"  Installing host tool: {pkg_name} ({match.get('version', '')})\n")
            dl_resp = client.get(archive_url)
            if dl_resp.status_code >= 400:
                log_cb(f"  host_tool {pkg_name}: download failed ({dl_resp.status_code})\n")
                return

            data = dl_resp.content
            head = data[:4]
            if head[:2] == b"PK":
                suffix, kind = ".zip", "zip"
            elif head[:2] == b"\x1f\x8b":
                suffix, kind = ".tar.gz", "gz"
            elif head == b"\x28\xb5\x2f\xfd":
                suffix, kind = ".tar.zst", "zst"
            else:
                log_cb(f"  host_tool {pkg_name}: unknown archive format\n")
                return
            tmp_archive = prefix / f"_hosttool_{pkg_name}{suffix}"
            tmp_archive.write_bytes(data)
            try:
                if kind == "zip":
                    with zipfile.ZipFile(tmp_archive) as zf:
                        zf.extractall(path=prefix)  # noqa: S202
                elif kind == "gz":
                    with tarfile.open(tmp_archive, "r:gz") as tf:
                        tf.extractall(path=prefix)  # noqa: S202
                elif kind == "zst":
                    import zstandard  # type: ignore[import-untyped]

                    with open(tmp_archive, "rb") as f_in:
                        dctx = zstandard.ZstdDecompressor()
                        with dctx.stream_reader(f_in) as reader:
                            with tarfile.open(fileobj=reader, mode="r|") as tf:
                                tf.extractall(path=prefix)  # noqa: S202
            finally:
                tmp_archive.unlink(missing_ok=True)

    def _execute_job(job: dict) -> None:
        """Execute a single build job.  The slot is released by the calling
        _run_job_guarded wrapper, not here."""
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
        dep_prefix: Path | None = None
        job_root: Path | None = None
        try:
            # 2. Download recipe
            _stream_log(job_id, f"Downloading recipe '{recipe_name}'...\n")
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

            # 3a. Install runtime dependencies into a shared prefix.
            # Re-ensure the work-dir root exists: on long-lived builders a /tmp
            # reaper can delete it between jobs, which would otherwise make
            # mkdtemp(dir=work_root) raise FileNotFoundError and fail the job.
            if work_root is not None:
                work_root.mkdir(parents=True, exist_ok=True)
            # Per-job isolation root. A single `builds submit` fans out to
            # several config/link variants of the SAME recipe that run
            # concurrently (max-jobs>1). Everything this job creates — dep
            # prefix, build work dir, output dir — lives under job_root so
            # the finally-cleanup can remove exactly this job's trees. The
            # previous cleanup globbed `cvcpkg-<recipe>-*` and deleted a
            # sibling variant's still-in-use work dir mid-build, which raised
            # FileNotFoundError at staging.mkdir() in the losing variant.
            job_root = Path(tempfile.mkdtemp(prefix=f"cvcpkg-job-{recipe_name}-", dir=work_root))
            dep_prefix = Path(
                tempfile.mkdtemp(prefix=f"cvcpkg-prefix-{recipe_name}-", dir=job_root)
            )
            log_cb = lambda text, _jid=job_id: _stream_log(_jid, text)  # noqa: E731
            _install_deps(
                recipe_dir, dep_prefix, job_platform, job_arch, job_config, job_link, log_cb
            )

            # 3a-2. Install cross-toolchains (e.g. emsdk for wasm)
            cross_env: dict[str, str] = {}
            if host_plat:
                cross_env = _install_cross_toolchains(
                    target_platform=job_platform,
                    host_platform=host_plat,
                    host_arch=arch,  # builder's native arch
                    prefix=dep_prefix,
                    log_cb=log_cb,
                    cache_dir=work_root,
                )

            # 3b. Build + package (output dir under the per-job root)
            output_dir = Path(tempfile.mkdtemp(prefix=f"cvcpkg-out-{recipe_name}-", dir=job_root))
            try:
                archive_path, sha256, size = pack_recipe(
                    recipe_dir,
                    platform=job_platform,
                    arch=job_arch,
                    config=job_config,
                    link=job_link,
                    prefix=dep_prefix,
                    output_dir=output_dir,
                    work_dir_root=job_root,
                    log_callback=log_cb,
                    host_platform=host_plat,
                    cross_toolchain_env=cross_env or None,
                )
                _stream_log(
                    job_id,
                    f"Build succeeded: {archive_path.name} ({size:,} bytes, sha256={sha256})\n",
                )
            except Exception as exc:
                error_message = f"build failed: {exc}\n{traceback.format_exc()}"
                _stream_log(job_id, error_message)
                raise

            # 4. Publish the archive to the server
            _stream_log(job_id, f"Publishing {archive_path.name}...\n")
            try:
                _publish_to_server(
                    server=base,
                    token=token,
                    archive_paths=[archive_path],
                    release_tag="",
                    chunked_threshold=10 * 1024 * 1024,
                    org=org_slug,
                )
                _stream_log(job_id, "Published successfully.\n")
            except click.ClickException as pub_exc:
                # Publish may raise if variant already exists on server.
                # The build itself succeeded - log the warning and continue.
                _stream_log(job_id, f"Publish warning: {pub_exc.format_message()}\n")

            result_url = f"{base}/v1/packages/{recipe_name}"

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
            click.echo(f"  [{job_id}] Failed: {recipe_name} - {exc}", err=True)

        finally:
            # Remove exactly this job's isolated tree (dep prefix, build work
            # dir, and output dir all live under job_root). Do NOT glob
            # cvcpkg-<recipe>-* across the shared work_root — concurrent
            # variant jobs of the same recipe would delete each other's
            # live work dirs (the FileNotFoundError-at-staging bug).
            if job_root is not None and job_root.is_dir():
                shutil.rmtree(job_root, ignore_errors=True)
            # NB: the slot count is released by _run_job_guarded's finally,
            # not here — so an early return from the claim step above (which
            # never reaches this try) still frees the slot.

    def _run_job_guarded(job: dict, token: int) -> None:
        """Thread entry point: run a job and ALWAYS release its slot.

        The caller reserves the slot (``_claim_slot``) before starting the
        thread; this wrapper's finally releases it no matter how
        ``_execute_job`` exits — normal return, exception, or the early
        ``return`` in its claim step.  Previously the release lived inside
        ``_execute_job``'s try/finally, which a failed-claim early return
        skipped, permanently leaking a slot (a max-jobs=2 builder wedged at
        2/2 after two failed claims and stopped taking work).
        """
        try:
            _execute_job(job)
        finally:
            _release_slot(token)

    # -- Self-update helper ---------------------------------

    def _self_update() -> None:
        """Pip-install the latest cvcpkg from the local git repo and re-exec.

        Looks for a libcvc-deps checkout by walking up from the
        installed package location.  Falls back to a ``git pull``
        in known paths.
        """
        import subprocess

        # When running under the Windows supervisor wrapper, hand the whole
        # update+restart cycle back to it: exit with a sentinel code so the
        # supervisor pulls the latest cvcpkg and relaunches us on fresh code.
        # This is what makes a server-pushed update apply without a manual
        # restart on Windows -- os.execv() cannot replace the process in
        # place there, and the freshly installed code otherwise only takes
        # effect on the next builder start.  The outer try/finally still runs
        # (in-flight jobs drain, builder unregisters, pidfile is removed) so
        # the successor starts clean past the single-instance guard.
        if sys.platform == "win32" and os.environ.get("CVCPKG_BUILDER_SUPERVISED"):
            click.echo(
                f"  self-update: requesting supervisor restart (exit {_SUPERVISOR_RESTART_CODE})."
            )
            raise SystemExit(_SUPERVISOR_RESTART_CODE)

        # Find the cvcpkg project root (where pyproject.toml lives)
        pkg_dir = Path(__file__).resolve().parent.parent  # cvcpkg package
        setup_dir = pkg_dir.parent  # src/
        # Walk up to find pyproject.toml
        candidates = [
            setup_dir.parent,  # repo root
            Path.home() / "libcvc-deps",
            Path("/root/libcvc-deps"),
        ]
        cvcpkg_dir: Path | None = None
        for c in candidates:
            if (c / "pyproject.toml").is_file():
                cvcpkg_dir = c
                break

        if cvcpkg_dir is None:
            click.echo("  self-update: cannot find cvcpkg source dir", err=True)
            return

        click.echo(f"  self-update: updating from {cvcpkg_dir}")
        try:
            # Pull latest code
            repo_root = cvcpkg_dir
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=repo_root,
                check=False,
                capture_output=True,
                timeout=60,
            )
            # Pip install
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--break-system-packages",
                    str(cvcpkg_dir),
                ],
                check=False,
                capture_output=True,
                timeout=120,
            )
            if sys.platform == "win32":
                # Windows has no in-place exec.  os.execv() here would spawn a
                # *new* process (CRT _P_OVERLAY semantics) and - because the
                # builder is launched via the ``cvcpkg.exe`` console-script -
                # re-exec ``python.exe cvcpkg.exe builder run ...``, which is
                # wrong and a source of stray/duplicate cvcpkg processes.  The
                # freshly pip-installed code is already on disk; it takes
                # effect the next time the scheduled task starts the builder.
                # Keep this single instance running on the current code rather
                # than spawning a broken successor.
                click.echo(
                    "  self-update: installed; new code applies on the next "
                    "builder restart (Windows).",
                )
                return
            click.echo("  self-update: installed, restarting...")
            # POSIX: replace the process image in place - same PID, no new
            # process, so the single-instance pidfile stays valid.
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            click.echo(f"  self-update failed: {exc}", err=True)

    # -- WebSocket helpers -----------------------------------

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
            click.echo("  websockets not installed - using HTTP long-poll", err=True)
            return False

        click.echo("Connecting via WebSocket...")
        try:
            with ws_sync.connect(_ws_url(), close_timeout=5) as ws:
                click.echo("WebSocket connected.")
                ws.settimeout(5)  # non-blocking reads with 5s timeout
                while not shutdown:
                    # Wall-clock budget: stop claiming, drain in-flight, exit.
                    if _past_deadline():
                        click.echo(
                            f"Max runtime reached ({max_runtime:.0f}s) - "
                            "stopping claims, finishing in-flight jobs..."
                        )
                        shutdown = True
                        break

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
                            token = _claim_slot()
                        t = threading.Thread(
                            target=_run_job_guarded, args=(job, token), daemon=True
                        )
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

                    elif msg_type == "builder.update":
                        server_ver = msg.get("version", "")
                        from cvcpkg import __version__

                        if server_ver and server_ver != __version__:
                            click.echo(f"  Server requests update: {__version__} -> {server_ver}")
                            _self_update()

                    elif msg_type == "job.timeout":
                        job_id = msg.get("job_id")
                        click.echo(
                            f"  [{job_id}] Server timed out job",
                            err=True,
                        )

            return True  # ran successfully (normal shutdown)

        except Exception as exc:
            click.echo(
                f"  WebSocket connection failed: {exc} - falling back to HTTP long-poll",
                err=True,
            )
            return False

    # -- Main loop -------------------------------------------

    last_heartbeat = 0.0
    heartbeat_interval = 60.0
    poll_interval = 5.0  # seconds between next-job polls

    # Time-boxed / drain-mode controls for ephemeral (CI) runners.
    run_deadline = (time.time() + max_runtime) if max_runtime else None

    def _past_deadline() -> bool:
        return run_deadline is not None and time.time() >= run_deadline

    # Drain-mode settle window: ``next-job`` only returns jobs the server
    # scheduler has already *dispatched to this builder*, and that loop runs
    # on an interval (~10s).  A freshly-registered drain builder would
    # otherwise get one 204 and exit before its first dispatch, orphaning
    # pending jobs.  Require the queue to stay empty for this long before
    # concluding it is truly drained; reset whenever a job is received.
    drain_settle_secs = float(os.environ.get("CVCPKG_DRAIN_SETTLE_SECS", "20"))
    drain_empty_since: float | None = None

    try:
        # Try WebSocket first (unless disabled).  Drain mode (--exit-when-empty)
        # needs the HTTP long-poll path: it returns 204 on an empty queue, which
        # is the signal to exit; the WebSocket path is push-only and never tells
        # us the queue is empty.
        use_ws = not no_websocket and not exit_when_empty
        if use_ws and not shutdown:
            ws_ok = _run_ws_loop()
            if ws_ok:
                # WebSocket ran until shutdown - skip HTTP loop
                use_ws = True
            else:
                use_ws = False

        # HTTP long-poll fallback
        while not shutdown:
            # Wall-clock budget: stop claiming, drain in-flight, exit.
            if _past_deadline():
                click.echo(
                    f"Max runtime reached ({max_runtime:.0f}s) - "
                    "stopping claims, finishing in-flight jobs..."
                )
                break

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
                # No job dispatched to us.  In drain mode, exit once nothing is
                # left to do: an empty queue AND no in-flight jobs (which could
                # still unlock dependent jobs when they finish).  But only after
                # the queue has stayed empty for the settle window, so we don't
                # exit before the scheduler has had a chance to dispatch pending
                # jobs to a just-registered builder.
                if exit_when_empty:
                    with jobs_lock:
                        inflight = current_jobs
                    if inflight == 0:
                        now = time.time()
                        if drain_empty_since is None:
                            drain_empty_since = now
                        elif now - drain_empty_since >= drain_settle_secs:
                            click.echo("Queue empty - exiting (--exit-when-empty).")
                            break
                    else:
                        drain_empty_since = None
                continue
            if resp.status_code >= 400:
                click.echo(
                    f"  poll failed: {resp.status_code}",
                    err=True,
                )
                time.sleep(poll_interval)
                continue

            job = resp.json()
            drain_empty_since = None  # got work; restart the settle window
            with jobs_lock:
                token = _claim_slot()

            # Run in a thread so we can keep heartbeating & polling.  The
            # guarded wrapper releases the slot on any exit path.
            t = threading.Thread(target=_run_job_guarded, args=(job, token), daemon=True)
            t.start()

    finally:
        # Wait for in-flight jobs
        deadline = time.time() + 300  # 5 min grace period
        while current_jobs > 0 and time.time() < deadline:
            click.echo(f"  Waiting for {current_jobs} in-flight job(s)...")
            time.sleep(5)

        click.echo("Shutting down - unregistering builder...")
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


@builder_group.command("unregister")
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
def builder_unregister(builder_id: int, server: str, token: str):
    """Unregister a builder by ID (admin-only)."""
    _api_request("delete", f"{server.rstrip('/')}/v1/builders/{builder_id}", token)
    click.echo(f"Builder #{builder_id} unregistered.")


@builder_group.command("logs")
@click.argument("builder_id", type=int, required=False)
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
    "--limit", type=int, default=20, show_default=True, help="Number of recent jobs to show."
)
@click.option(
    "--status", default=None, help="Filter by job status (e.g. running/failed/succeeded)."
)
@click.option(
    "--tail",
    type=int,
    default=0,
    metavar="LINES",
    help="Also print the last LINES of the most recent job's log.",
)
@click.option(
    "--job", type=int, default=None, help="Tail this specific job ID instead of the latest."
)
def builder_logs(
    builder_id: int | None,
    server: str,
    token: str,
    limit: int,
    status: str | None,
    tail: int,
    job: int | None,
):
    """Show recent build activity, optionally for a single builder.

    Lists the most recent build jobs (newest first) and, with ``--tail``,
    prints the tail of a job's log - a lightweight alternative to the full
    ``cvcpkg builds monitor`` view.
    """
    import httpx

    params: dict[str, str] = {"limit": str(max(1, limit))}
    if builder_id is not None:
        params["builder_id"] = str(builder_id)
    if status:
        params["status"] = status

    base = server.rstrip("/")
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{base}/v1/builds",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(f"server returned {resp.status_code}: {detail}")

    jobs = resp.json().get("jobs", [])
    # Newest first by submission time.
    jobs = sorted(jobs, key=lambda j: j.get("submitted_at") or "", reverse=True)

    scope = f" for builder #{builder_id}" if builder_id is not None else ""
    if not jobs:
        click.echo(f"No build jobs found{scope}.")
        return

    click.echo(f"Recent build activity{scope}:")
    click.echo(
        f"{'Job':>6}  {'Recipe':<24} {'Plat/Arch':<16} {'Status':<10} {'Builder':>7}  Submitted"
    )
    click.echo("-" * 90)
    for j in jobs:
        pa = f"{j.get('platform', '?')}/{j.get('arch', '?')}"
        bid = j.get("builder_id")
        click.echo(
            f"{j['id']:>6}  {j.get('recipe_name', '?'):<24} {pa:<16} "
            f"{j.get('status', '?'):<10} {('#' + str(bid)) if bid else '-':>7}  "
            f"{j.get('submitted_at', '')}"
        )

    if tail > 0:
        target = job if job is not None else jobs[0]["id"]
        with httpx.Client(timeout=30) as client:
            log_resp = client.get(
                f"{base}/v1/builds/{target}/log",
                headers={"Authorization": f"Bearer {token}"},
            )
        if log_resp.status_code == 404:
            click.echo(f"\n(no log available for job #{target})")
            return
        if log_resp.status_code >= 400:
            raise click.ClickException(
                f"server returned {log_resp.status_code} fetching log for job #{target}"
            )
        lines = log_resp.text.splitlines()
        click.echo(f"\n-- log tail: job #{target} (last {min(tail, len(lines))} lines) --")
        for line in lines[-tail:]:
            click.echo(line)
