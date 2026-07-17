"""Delegate Windows-target builds to the Windows host of a WSL instance.

A cvcpkg builder running inside WSL2 registers as ``linux/x86_64`` with
``--cross-platform windows``.  When it receives a ``windows/x86_64`` job,
the recipe's normal Windows build script (``build.ps1``) is executed **on
the Windows host** through WSL interop (``powershell.exe`` is directly
invocable from inside the distro), so the host's MSVC toolchain does the
compilation while source fetch, packaging, and publishing stay on the
Linux side — the same split the wasm cross-builds use, with the Windows
host playing the role of the toolchain.

Two file-exchange modes:

``direct``
    The host builds straight off the WSL filesystem via the
    ``\\\\wsl.localhost\\<distro>\\...`` UNC paths that ``wslpath -w``
    produces.  No copying, but host tool I/O crosses the 9P boundary
    (slow for compile-heavy builds) and some build tools mishandle UNC
    working directories.

``exchange``
    Sources, the recipe bundle, and the dependency prefix are staged
    into a directory in the Windows **user profile**
    (``%USERPROFILE%\\cvcpkg-winhost`` by default), the host builds on
    native NTFS, and the install tree is synced back for the Linux side
    to pack and publish.

``auto`` (default) selects ``exchange``: although the host can usually
read WSL files via ``\\\\wsl.localhost``, cmd.exe cannot use UNC working
directories and CMake's MSVC link step runs through cmd.exe, so direct
mode only suits recipes whose toolchain tolerates UNC cwds — opt in
explicitly when that holds.

Environment knobs (all optional):

``CVCPKG_WINHOST``            ``0``/``false`` disables delegation entirely.
``CVCPKG_WINHOST_MODE``       ``auto`` | ``direct`` | ``exchange``.
``CVCPKG_WINHOST_EXCHANGE``   Windows path of the exchange root
                              (default ``%USERPROFILE%\\cvcpkg-winhost``).
``CVCPKG_WINHOST_POWERSHELL`` WSL path of the host ``powershell.exe``.
``CVCPKG_WINHOST_JOBS``       Overrides ``CVC_JOBS`` for the host build.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from cvcpkg.errors import CvcpkgError

if TYPE_CHECKING:  # pragma: no cover - import cycle guard (builder imports us)
    from cvcpkg.builder import BuildContext, MatrixEntry


class WinhostError(CvcpkgError):
    """WSL -> Windows host delegation failed."""


_PLAT_ALIASES = {"win": "windows"}

# Name of the host-side runner script shipped in recipes/_common/.  It
# travels with every recipe bundle (bundles include _common/ alongside
# the recipe dir), so builders pick up runner updates with recipe pushes
# exactly like env-windows.ps1 updates.
RUNNER_NAME = "winhost-run-job.ps1"

# Resolved interop settings, cached per process after the first probe.
_interop_cache: dict | None = None


def _norm_plat(platform: str) -> str:
    return _PLAT_ALIASES.get(platform, platform)


def _env_flag_disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "off", "no")


def is_wsl() -> bool:
    """Return True when running inside a WSL distro."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def should_delegate(platform: str, host_platform: str) -> bool:
    """True when this build should run on the Windows host via interop.

    Requires: target is windows, we are cross-compiling (a non-windows
    host_platform was selected), we are on Linux inside WSL, and the
    feature has not been disabled via ``CVCPKG_WINHOST=0``.
    """
    if _env_flag_disabled("CVCPKG_WINHOST"):
        return False
    if _norm_plat(platform) != "windows":
        return False
    host = _norm_plat(host_platform) if host_platform else ""
    if host in ("", "windows"):
        return False  # native build, not a cross build
    if sys.platform != "linux":
        return False
    return is_wsl()


# ── Interop plumbing ────────────────────────────────────────────


def _powershell_candidates() -> list[str]:
    """Possible WSL-side paths of the host's powershell.exe."""
    override = os.environ.get("CVCPKG_WINHOST_POWERSHELL", "")
    if override:
        return [override]
    cands: list[str] = []
    found = shutil.which("powershell.exe")
    if found:
        cands.append(found)
    cands += sorted(glob.glob("/mnt/*/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    # De-dup, preserve order.
    seen: set[str] = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def _interop_socket_candidates() -> list[str]:
    """Possible WSL_INTEROP socket paths, most promising first.

    A process launched by ``wsl.exe`` inherits ``WSL_INTEROP``; a
    systemd service does not, so fall back to the init socket
    (``/run/WSL/1_interop``, present on systemd-enabled distros) and
    then to any session socket, newest first.
    """
    cands: list[str] = []
    env_sock = os.environ.get("WSL_INTEROP", "")
    if env_sock:
        cands.append(env_sock)
    cands.append("/run/WSL/1_interop")
    others = [p for p in glob.glob("/run/WSL/*_interop") if p not in cands]
    others.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    cands += others
    return [c for c in cands if os.path.exists(c)]


def _resolve_interop(force: bool = False) -> dict:
    """Find a working (powershell.exe, WSL_INTEROP) combination.

    Returns ``{"powershell": <path>, "env": {<extra env>}}``.  Cached
    per process; pass ``force=True`` to re-probe.
    """
    global _interop_cache
    if _interop_cache is not None and not force:
        return _interop_cache

    ps_paths = _powershell_candidates()
    if not ps_paths:
        raise WinhostError(
            "powershell.exe not found (searched PATH and /mnt/*/Windows/System32). "
            "Is the Windows drive automounted?  Set CVCPKG_WINHOST_POWERSHELL to "
            "the WSL path of powershell.exe."
        )

    sockets = _interop_socket_candidates()
    # An inherited-interop process may work with no explicit socket too.
    attempts: list[tuple[str, str | None]] = []
    for ps in ps_paths:
        attempts.append((ps, None))
        attempts += [(ps, s) for s in sockets]

    last_err = ""
    for ps, sock in attempts:
        env = os.environ.copy()
        if sock:
            env["WSL_INTEROP"] = sock
        try:
            r = subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
                env=env,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = str(exc)
            continue
        if r.returncode == 0:
            _interop_cache = {
                "powershell": ps,
                "env": {"WSL_INTEROP": sock} if sock else {},
            }
            return _interop_cache
        last_err = r.stderr.decode(errors="replace").strip() or f"rc={r.returncode}"

    raise WinhostError(
        "WSL interop is not reachable: powershell.exe failed from every "
        f"candidate interop socket ({last_err or 'no sockets found'}). "
        "The distro may have been started without interop, or all interop "
        "sockets are dead — ensure /etc/wsl.conf has [interop] enabled=true "
        "and the distro was launched by wsl.exe (see the winhost docs)."
    )


def winhost_available() -> bool:
    """Best-effort check that host delegation can work from here."""
    if not is_wsl():
        return False
    try:
        _resolve_interop()
        return True
    except WinhostError:
        return False


def _run_ps(command: str, timeout: float = 120) -> str:
    """Run a PowerShell command on the host, return stripped stdout."""
    io = _resolve_interop()
    env = os.environ.copy()
    env.update(io["env"])
    r = subprocess.run(
        [io["powershell"], "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise WinhostError(
            f"host powershell command failed (rc={r.returncode}): "
            f"{r.stderr.decode(errors='replace').strip()[:500]}"
        )
    return r.stdout.decode(errors="replace").replace("\r", "").strip()


def _wslpath(flag: str, path: str) -> str:
    """Translate a path with wslpath (-w -> Windows form, -u -> WSL form)."""
    wslpath = shutil.which("wslpath") or "/usr/bin/wslpath"
    r = subprocess.run([wslpath, flag, path], capture_output=True, text=True)
    if r.returncode != 0:
        raise WinhostError(f"wslpath {flag} {path!r} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def win_path(path: Path | str) -> str:
    """WSL path -> Windows path (\\\\wsl.localhost UNC for ext4 paths)."""
    return _wslpath("-w", str(path))


def wsl_path(path: str) -> Path:
    """Windows path -> WSL path (e.g. C:\\Users\\x -> /mnt/c/Users/x)."""
    return Path(_wslpath("-u", path))


def win_user_profile() -> str:
    """The host user's profile directory (C:\\Users\\<user>)."""
    profile = _run_ps("$env:USERPROFILE")
    if not profile:
        raise WinhostError("could not resolve %USERPROFILE% on the Windows host")
    return profile


def _exchange_override() -> str:
    """Read and sanity-check ``CVCPKG_WINHOST_EXCHANGE``.

    Accepts backslash or forward-slash Windows paths (normalized to the
    backslash form, no trailing separator).  Detects the classic systemd
    ``EnvironmentFile`` mangling — unescaped backslashes are stripped
    while parsing, turning ``C:\\Users\\x`` into ``C:Usersx`` — and
    fails with an actionable message instead of an opaque wslpath error
    on every windows job.
    """
    raw = os.environ.get("CVCPKG_WINHOST_EXCHANGE", "").strip().strip('"')
    if not raw:
        return ""
    if re.match(r"^[A-Za-z]:(?![/\\])", raw):
        raise WinhostError(
            f"CVCPKG_WINHOST_EXCHANGE looks mangled: {raw!r} — drive colon "
            "with no path separator after it. If this is set via a systemd "
            "EnvironmentFile (e.g. /etc/cvcpkg/builder.env), systemd strips "
            "unescaped backslashes; write the path with forward slashes "
            "(C:/Users/<user>/cvcpkg-winhost) or doubled backslashes."
        )
    return raw.replace("/", "\\").rstrip("\\")


# ── Path fix-ups ────────────────────────────────────────────────


def _rewrite_prefix_references(root: Path, old_prefix: str, new_prefix: str) -> int:
    """Point .pc/.cmake files at the prefix path the host build will see.

    The builder extracts dependency packages on the Linux side and
    rewrites their ``prefix=`` lines to the Linux dep-prefix path; the
    Windows host needs the same files to reference the Windows-visible
    location instead.  Handles both forward- and back-slash spellings.
    Returns the number of files rewritten.
    """
    if not root.is_dir():
        return 0
    old_fwd = old_prefix.replace("\\", "/")
    new_fwd = new_prefix.replace("\\", "/")
    count = 0
    for f in list(root.rglob("*.pc")) + list(root.rglob("*.cmake")):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if old_fwd not in text and old_prefix not in text:
            continue
        new_text = text.replace(old_fwd, new_fwd)
        if old_prefix != old_fwd:
            new_text = new_text.replace(old_prefix, new_fwd)
        if new_text != text:
            f.write_text(new_text, encoding="utf-8")
            count += 1
    return count


# ── Job staging + execution ─────────────────────────────────────


def _job_env(
    ctx: BuildContext,
    matrix: MatrixEntry,
    paths: dict[str, str],
) -> dict[str, str]:
    """Environment for the host-side build script (Windows path forms).

    Mirrors the variables ``_build_env`` provides to local builds, with
    directory variables translated to what the host sees.  PATH handling
    (deps\\bin etc.) is done by the runner on the host, where the real
    host PATH is known.
    """
    build_type = "Release" if ctx.config == "release" else "Debug"
    env: dict[str, str] = {
        "CVC_PREFIX": paths["install"],
        "CVC_SOURCE_DIR": paths["source"],
        "CVC_BUILD_DIR": paths["build"],
        "CVC_INSTALL_DIR": paths["install"],
        "CVC_DEPS_PREFIX": paths["deps"],
        "CVC_RECIPE_DIR": paths["recipe"],
        # The build closure (host tools, staged source packages) lives in a
        # separate prefix that must also be visible host-side.  Falls back to
        # the deps prefix when the build prefix is not separated.
        "CVC_BUILD_PREFIX": paths.get("build_prefix") or paths["deps"],
        "CVC_PLATFORM": "windows",
        "CVC_CONFIG": ctx.config,
        "CVC_BUILD_TYPE": build_type,
        "CVC_LINK": ctx.link,
        "CVC_COMPONENT": ctx.recipe.name,
        "CVC_VERSION": ctx.recipe.upstream_version,
        "CMAKE_BUILD_TYPE": build_type,
        "BUILD_SHARED_LIBS": "ON" if ctx.link == "shared" else "OFF",
        # Marker so recipes/tests can detect host-delegated builds.
        "CVC_WINHOST": "1",
    }
    jobs = os.environ.get("CVCPKG_WINHOST_JOBS", "")
    if jobs:
        env["CVC_JOBS"] = jobs
    env.update(matrix.env)
    return env


def _copytree(src: Path, dst: Path) -> None:
    """Copy a tree onto the Windows mount, materializing symlinks.

    NTFS/drvfs can't represent the symlinks that show up in unpacked
    source tarballs; copying the link target's content is what the host
    toolchain needs anyway.
    """
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)


def _stream_host_process(
    cmd: list[str],
    env: dict[str, str],
    log_callback: Callable[[str], None] | None,
) -> int:
    """Run the host process, teeing CRLF-normalized output to the log."""
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _flush_bytes = 8192
    buf: list[str] = []
    buf_size = 0
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if line.endswith("\r"):
            line = line[:-1] + "\n"
        sys.stdout.write(line)
        if log_callback:
            buf.append(line)
            buf_size += len(line)
            if buf_size >= _flush_bytes:
                log_callback("".join(buf))
                buf.clear()
                buf_size = 0
    if log_callback and buf:
        log_callback("".join(buf))
    return proc.wait()


def _probe_direct_access(probe_dir: Path) -> bool:
    """Can the Windows host see this WSL directory via \\\\wsl.localhost?"""
    token = uuid.uuid4().hex
    probe = probe_dir / f".cvc-winhost-probe-{token}"
    try:
        probe.write_text(token)
        win = win_path(probe)
        out = _run_ps(f"if (Test-Path -LiteralPath '{win}') {{ 'yes' }} else {{ 'no' }}")
        return out == "yes"
    except (WinhostError, OSError):
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _find_runner(recipe_dir: Path) -> Path:
    """Locate winhost-run-job.ps1 next to the recipe (in _common/)."""
    candidates = [
        recipe_dir.parent / "_common" / RUNNER_NAME,
        recipe_dir / "_common" / RUNNER_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise WinhostError(
        f"{RUNNER_NAME} not found alongside the recipe "
        f"(looked in {candidates[0].parent} and {candidates[1].parent}). "
        "The recipe bundle predates winhost support — push current recipes "
        "to the server (recipes/_common ships with every bundle)."
    )


def run_winhost_build(
    ctx: BuildContext,
    matrix: MatrixEntry,
    script: Path,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Execute *script* (the recipe's Windows build script) on the host.

    Stages the job per the selected mode, invokes the ``_common``
    runner through interop, streams output, and (in exchange mode)
    syncs the install tree back to ``ctx.install_dir``.  Raises
    :class:`WinhostError` on failure.
    """
    import json

    if script.suffix != ".ps1":
        raise WinhostError(
            f"winhost delegation only supports .ps1 build scripts, got {script.name}. "
            "Add a 'platform: windows' matrix entry with a PowerShell script."
        )

    def _log(msg: str) -> None:
        print(msg)
        if log_callback:
            log_callback(msg + "\n")

    io = _resolve_interop()
    mode = os.environ.get("CVCPKG_WINHOST_MODE", "auto").strip().lower()
    if mode not in ("auto", "direct", "exchange"):
        raise WinhostError(f"invalid CVCPKG_WINHOST_MODE: {mode!r}")

    if mode == "auto":
        # Exchange is the reliable default: even though the host can
        # usually read WSL files via \\wsl.localhost, CMake's MSVC link
        # step runs through cmd.exe, which cannot use a UNC working
        # directory — direct mode is therefore opt-in for recipes whose
        # toolchain tolerates UNC cwds.
        mode = "exchange"

    if mode == "direct" and not _probe_direct_access(ctx.work_dir):
        raise WinhostError(
            "CVCPKG_WINHOST_MODE=direct but the Windows host cannot access "
            f"this distro's files ({ctx.work_dir} was not visible via "
            "\\\\wsl.localhost). Use exchange mode instead."
        )

    runner_src = _find_runner(ctx.recipe.recipe_dir)

    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    ctx.install_dir.mkdir(parents=True, exist_ok=True)

    exchange_job_dir: Path | None = None
    try:
        if mode == "direct":
            # Host builds straight off the WSL filesystem via UNC paths.
            paths = {
                "source": win_path(ctx.source_dir.resolve()),
                "build": win_path(ctx.build_dir.resolve()),
                "install": win_path(ctx.install_dir.resolve()),
                "deps": win_path(ctx.prefix.resolve()),
                "recipe": win_path(ctx.recipe.recipe_dir),
            }
            # The build closure (host tools, staged source packages) lives in a
            # separate prefix; the host build must see it too.
            if ctx.build_prefix is not None and ctx.build_prefix != ctx.prefix:
                paths["build_prefix"] = win_path(ctx.build_prefix.resolve())
            # Dependency .pc/.cmake files reference Linux paths; point
            # them at the UNC form the host toolchain will resolve.
            n = _rewrite_prefix_references(
                Path(ctx.prefix), str(ctx.prefix.resolve()), paths["deps"]
            )
            if n:
                _log(f"cvcpkg-winhost: rewrote {n} dep metadata file(s) to UNC paths")
            runner_win = win_path(runner_src)
            job_dir_wsl = ctx.work_dir
        else:
            # Stage into the exchange directory in the Windows profile.
            exchange_win = _exchange_override()
            if not exchange_win:
                exchange_win = win_user_profile().rstrip("\\") + "\\cvcpkg-winhost"
            exchange_root = wsl_path(exchange_win)
            job_name = f"{ctx.recipe.name}-{uuid.uuid4().hex[:12]}"
            exchange_job_dir = exchange_root / "jobs" / job_name
            job_dir_wsl = exchange_job_dir
            _log(f"cvcpkg-winhost: staging job into {exchange_win}\\jobs\\{job_name}")

            (exchange_job_dir / "build").mkdir(parents=True, exist_ok=True)
            (exchange_job_dir / "install").mkdir(parents=True, exist_ok=True)
            _copytree(ctx.source_dir, exchange_job_dir / "source")

            # Recipe dir + sibling _common (for env-windows.ps1 etc.).
            recipe_dst = exchange_job_dir / "recipe" / ctx.recipe.recipe_dir.name
            _copytree(ctx.recipe.recipe_dir, recipe_dst)
            common_src = ctx.recipe.recipe_dir.parent / "_common"
            if common_src.is_dir():
                _copytree(common_src, exchange_job_dir / "recipe" / "_common")

            deps_dst = exchange_job_dir / "deps"
            if Path(ctx.prefix).is_dir():
                _copytree(Path(ctx.prefix), deps_dst)
            else:
                deps_dst.mkdir(parents=True, exist_ok=True)

            # Stage the build closure (host tools, staged source packages) so
            # the host build can reach it at CVC_BUILD_PREFIX -- e.g. a source
            # package's tree at <build-prefix>\src\<name>.  Only when the build
            # prefix is actually separated from the deliverable prefix.
            bp_dst: Path | None = None
            _separated = ctx.build_prefix is not None and ctx.build_prefix != ctx.prefix
            if _separated and Path(ctx.build_prefix).is_dir():
                bp_dst = exchange_job_dir / "build-prefix"
                _copytree(Path(ctx.build_prefix), bp_dst)

            job_win = exchange_win.rstrip("\\") + "\\jobs\\" + job_name
            paths = {
                "source": job_win + "\\source",
                "build": job_win + "\\build",
                "install": job_win + "\\install",
                "deps": job_win + "\\deps",
                "recipe": job_win + "\\recipe\\" + ctx.recipe.recipe_dir.name,
            }
            if bp_dst is not None:
                paths["build_prefix"] = job_win + "\\build-prefix"
            n = _rewrite_prefix_references(deps_dst, str(Path(ctx.prefix).resolve()), paths["deps"])
            if n:
                _log(f"cvcpkg-winhost: rewrote {n} dep metadata file(s) to exchange paths")
            if bp_dst is not None:
                n = _rewrite_prefix_references(
                    bp_dst, str(Path(ctx.build_prefix).resolve()), paths["build_prefix"]
                )
                if n:
                    _log(f"cvcpkg-winhost: rewrote {n} build-prefix metadata file(s)")
            runner_win = paths["recipe"].rsplit("\\", 1)[0] + "\\_common\\" + RUNNER_NAME

        job = {
            "schema": 1,
            "mode": mode,
            "recipe": ctx.recipe.name,
            "script": matrix.script,
            "env": _job_env(ctx, matrix, paths),
        }
        job_file = job_dir_wsl / "winhost-job.json"
        job_file.write_text(json.dumps(job, indent=2), encoding="utf-8")
        job_file_win = (
            win_path(job_file)
            if mode == "direct"
            else paths["build"].rsplit("\\", 1)[0] + "\\winhost-job.json"
        )

        header = (
            f"cvcpkg-winhost: building {ctx.recipe.name} {ctx.recipe.full_version} "
            f"on the Windows host (mode={mode}, script={matrix.script})"
        )
        _log(header)

        env = os.environ.copy()
        env.update(io["env"])
        rc = _stream_host_process(
            [
                io["powershell"],
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                runner_win,
                "-JobFile",
                job_file_win,
            ],
            env,
            log_callback,
        )
        if rc != 0:
            raise WinhostError(f"host build of {ctx.recipe.name} failed with exit code {rc}")

        if mode == "exchange":
            # Sync the install tree back for packaging on the Linux side.
            assert exchange_job_dir is not None
            installed = exchange_job_dir / "install"
            if not installed.is_dir():
                raise WinhostError("host build produced no install directory")
            shutil.copytree(installed, ctx.install_dir, dirs_exist_ok=True)
            file_count = sum(1 for p in ctx.install_dir.rglob("*") if p.is_file())
            _log(f"cvcpkg-winhost: synced install tree back ({file_count} files)")
            if file_count == 0:
                raise WinhostError("host build installed no files")
    finally:
        if exchange_job_dir is not None and not ctx.keep_build_dir:
            shutil.rmtree(exchange_job_dir, ignore_errors=True)
