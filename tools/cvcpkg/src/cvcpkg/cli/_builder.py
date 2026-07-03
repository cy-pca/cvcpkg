"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._publish import _publish_to_server
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
        # cache_dir may have been reaped (e.g. OpenBSD /tmp cleanup) between
        # builder startup and this call; recreate before writing.
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(resp.content)

        # Extract
        extract_dir = cache_dir / recipe_name
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        if extract_dir.exists():
            # rmtree left remnants — force remove
            import subprocess

            subprocess.run(["rm", "-rf", str(extract_dir)], check=False)
        extract_dir.mkdir(parents=True, exist_ok=True)
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

        Queries the server catalog for each runtime dep, downloads the
        matching archive, and extracts it into the shared prefix so
        that dependent builds can find headers/libraries.
        """
        import yaml as _yaml

        recipe_yaml = recipe_dir / "recipe.yaml"
        if not recipe_yaml.is_file():
            return
        data = _yaml.safe_load(recipe_yaml.read_text())
        deps_block = data.get("depends", {})

        dep_names: list[str] = []
        for key in ("runtime", "build"):
            for dep in deps_block.get(key, []) or []:
                if isinstance(dep, str):
                    dep_names.append(dep)
                elif isinstance(dep, dict):
                    # Respect platform filter
                    plats = dep.get("platforms")
                    if plats and job_platform not in plats:
                        continue
                    dep_names.append(dep["name"])

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
                log_cb(f"  Installing dep: {dep_name} ({match.get('version','')})\n")
                dl_resp = client.get(archive_url)
                if dl_resp.status_code >= 400:
                    log_cb(f"  dep {dep_name}: download failed ({dl_resp.status_code})\n")
                    continue

                # Extract into prefix.  The catalog's archive_url suffix
                # (typically .tar.zst) is purely cosmetic — the server
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
    ) -> dict[str, str]:
        """Install cross-toolchain packages and return their env vars.

        Queries the server for recipes that provide a cross-toolchain
        for *target_platform* (e.g. emsdk for wasm, wasi-sdk for wasi).
        Downloads the pre-built host-platform package and extracts it.
        Returns a merged ``cross_toolchain_env`` dict with ``${PREFIX}``
        already resolved to the actual *prefix* path.
        """
        import yaml as _yaml

        # Map target platforms → known toolchain recipe names.
        # The builder fetches the recipe bundle to read cross_toolchain.env
        # dynamically, but needs to know which recipes to look for.
        _TOOLCHAIN_MAP: dict[str, list[str]] = {
            "wasm": ["emsdk"],
            "wasi": ["wasi-sdk"],
        }
        toolchain_names = _TOOLCHAIN_MAP.get(target_platform, [])
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

                log_cb(f"  Installing cross-toolchain: {tc_name} ({match.get('version', '')})\n")
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
                tmp_archive = prefix / f"_toolchain_{tc_name}{suffix}"
                tmp_archive.write_bytes(tc_bytes)
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
                    log_cb(f"  toolchain {tc_name}: extract failed ({exc})\n")
                    continue
                finally:
                    tmp_archive.unlink(missing_ok=True)

            # 3. Resolve env templates
            for var, tpl in ct_env.items():
                merged_env[var] = tpl.replace("${PREFIX}", str(prefix))

            log_cb(
                f"  Toolchain {tc_name} installed ({', '.join(f'{k}={v}' for k, v in ct_env.items())})\n"
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
        dep_prefix: Path | None = None
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

            # 3a. Install runtime dependencies into a shared prefix
            dep_prefix = Path(
                tempfile.mkdtemp(prefix=f"cvcpkg-prefix-{recipe_name}-", dir=work_root)
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
                )

            # 3b. Build + package
            output_dir = Path(tempfile.mkdtemp(prefix=f"cvcpkg-out-{recipe_name}-"))
            try:
                archive_path, sha256, size = pack_recipe(
                    recipe_dir,
                    platform=job_platform,
                    arch=job_arch,
                    config=job_config,
                    link=job_link,
                    prefix=dep_prefix,
                    output_dir=output_dir,
                    work_dir_root=work_root,
                    log_callback=log_cb,
                    host_platform=host_plat,
                    cross_toolchain_env=cross_env or None,
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
                # The build itself succeeded — log the warning and continue.
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
            click.echo(f"  [{job_id}] Failed: {recipe_name} — {exc}", err=True)

        finally:
            # Clean up output dir, dep prefix, and any leaked work dirs
            if archive_path and archive_path.parent.is_dir():
                shutil.rmtree(archive_path.parent, ignore_errors=True)
            if dep_prefix and dep_prefix.is_dir():
                shutil.rmtree(dep_prefix, ignore_errors=True)
            # build_recipe creates cvcpkg-{name}-* work dirs that leak on failure
            cleanup_root = work_root or Path(tempfile.gettempdir())
            for stale in cleanup_root.glob(f"cvcpkg-{recipe_name}-*"):
                if stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)
            with jobs_lock:
                current_jobs -= 1

    # ── Self-update helper ─────────────────────────────────

    def _self_update() -> None:
        """Pip-install the latest cvcpkg from the local git repo and re-exec.

        Looks for a libcvc-deps checkout by walking up from the
        installed package location.  Falls back to a ``git pull``
        in known paths.
        """
        import subprocess

        # Find the tools/cvcpkg directory
        pkg_dir = Path(__file__).resolve().parent.parent  # cvcpkg package
        setup_dir = pkg_dir.parent  # src/
        # Walk up to find pyproject.toml
        candidates = [
            setup_dir.parent,  # tools/cvcpkg
            Path.home() / "libcvc-deps" / "tools" / "cvcpkg",
            Path("/root/libcvc-deps/tools/cvcpkg"),
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
            repo_root = cvcpkg_dir.parent.parent  # libcvc-deps root
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
            click.echo("  self-update: installed, restarting…")
            # Re-exec with the same arguments
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            click.echo(f"  self-update failed: {exc}", err=True)

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

                    elif msg_type == "builder.update":
                        server_ver = msg.get("version", "")
                        from cvcpkg import __version__

                        if server_ver and server_ver != __version__:
                            click.echo(f"  Server requests update: {__version__} → {server_ver}")
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
