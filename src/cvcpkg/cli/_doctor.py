# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""``cvcpkg doctor`` — diagnose the local environment.

Reports whether the tools cvcpkg needs for building and installing
prebuilt bundles are present, and (optionally) whether a cvcpkg-server
is reachable.  Intended as the first thing a new user runs after
``pip install cvcpkg`` to confirm their machine is ready.
"""

from __future__ import annotations

import platform as _platform
import shutil
import subprocess
import sys

import click

from cvcpkg.cli import cli

# ── Check result model ──────────────────────────────────────────

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"

_SYMBOL = {_OK: "✓", _WARN: "!", _FAIL: "✗"}


class _Check:
    __slots__ = ("name", "status", "detail")

    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status
        self.detail = detail


def _tool_version(exe: str, args: tuple[str, ...] = ("--version",)) -> str:
    """Return the first line of ``exe --version`` output, or '' on failure."""
    try:
        out = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (out.stdout or out.stderr or "").strip()
    return text.splitlines()[0].strip() if text else ""


def _check_python() -> _Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        return _Check("Python", _FAIL, f"{ver} (cvcpkg requires >= 3.10)")
    return _Check("Python", _OK, ver)


def _check_tool(
    label: str,
    candidates: list[str],
    *,
    required: bool,
    purpose: str,
    version_args: tuple[str, ...] = ("--version",),
) -> _Check:
    """Resolve the first available executable from *candidates*."""
    for exe in candidates:
        path = shutil.which(exe)
        if path:
            ver = _tool_version(exe, version_args)
            detail = ver or path
            return _Check(label, _OK, detail)
    status = _FAIL if required else _WARN
    return _Check(label, status, f"not found — needed for {purpose}")


def _check_compiler() -> _Check:
    """Detect a working C/C++ compiler for the current platform."""
    if sys.platform in ("win32", "cygwin"):
        candidates = ["cl", "clang-cl", "clang", "gcc"]
    elif sys.platform == "darwin":
        candidates = ["clang", "cc", "gcc"]
    else:
        candidates = ["cc", "gcc", "clang"]
    for exe in candidates:
        if shutil.which(exe):
            # cl.exe prints its banner on stderr with no --version flag.
            ver = _tool_version(exe) or exe
            return _Check("C/C++ compiler", _OK, f"{exe} ({ver})" if ver != exe else exe)
    return _Check(
        "C/C++ compiler",
        _FAIL,
        "no C/C++ compiler found — needed to build recipes from source",
    )


def _check_server(server: str) -> _Check:
    """Check that a cvcpkg-server responds on /healthz."""
    import httpx

    url = f"{server.rstrip('/')}/healthz"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
    except Exception as exc:  # noqa: BLE001 — surface any connection error
        return _Check("Server", _FAIL, f"cannot reach {server}: {exc}")
    if resp.status_code != 200:
        return _Check("Server", _FAIL, f"{server} returned {resp.status_code}")
    try:
        data = resp.json()
        ver = data.get("version", "?")
        pkgs = data.get("packages_count", "?")
        return _Check("Server", _OK, f"{server} (v{ver}, {pkgs} packages)")
    except Exception:  # noqa: BLE001
        return _Check("Server", _OK, server)


@cli.command("doctor")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default="",
    metavar="URL",
    help="Also check that this cvcpkg-server is reachable.  [env: CVCPKG_SERVER_URL]",
)
def doctor(server: str) -> None:
    """Check the local environment for building and installing packages.

    Verifies the host toolchain (Python, CMake, Ninja, a C/C++ compiler,
    git) and, when ``--server`` is given, that a cvcpkg-server is
    reachable.  Exits non-zero if any required check fails so it can be
    used as a CI/setup gate.
    """
    checks: list[_Check] = [
        _check_python(),
        _check_tool("pip", ["pip", "pip3"], required=False, purpose="installing cvcpkg itself"),
        _check_tool("CMake", ["cmake"], required=True, purpose="configuring recipe builds"),
        _check_tool("Ninja", ["ninja"], required=False, purpose="faster recipe builds"),
        _check_compiler(),
        _check_tool("git", ["git"], required=False, purpose="fetching git-based sources"),
    ]
    if server:
        checks.append(_check_server(server))

    click.echo("cvcpkg doctor")
    click.echo(
        f"  Host: {_platform.system()} {_platform.machine()} (Python {sys.version.split()[0]})"
    )
    click.echo("")

    width = max(len(c.name) for c in checks)
    for c in checks:
        sym = _SYMBOL[c.status]
        line = f"  {sym}  {c.name.ljust(width)}  {c.detail}".rstrip()
        click.echo(line)

    click.echo("")
    fails = [c for c in checks if c.status == _FAIL]
    warns = [c for c in checks if c.status == _WARN]
    if fails:
        click.echo(
            f"{len(fails)} problem(s) found — cvcpkg may not work correctly.",
            err=True,
        )
        # main() invokes the CLI with standalone_mode=False, where Click
        # *returns* Exit codes instead of raising them; SystemExit is what
        # propagates cleanly to a non-zero process exit.
        raise SystemExit(1)
    if warns:
        click.echo(f"Ready, with {len(warns)} optional tool(s) missing.")
    else:
        click.echo("Everything looks good.")
