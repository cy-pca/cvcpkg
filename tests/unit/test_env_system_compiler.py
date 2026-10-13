"""The BSD and macOS env scripts pick the system compiler by absolute path.

The builder puts the build prefix's and deps prefix's ``bin/`` ahead of the
system directories on PATH, and cvcpkg's llvm, llvm18 and llvm20 bundles ship a
``clang``.  A bare ``CC=clang`` therefore compiled with cvcpkg's clang (and
linked with ld64.lld) whenever one of them was in the prefix: a macOS pack-all
shard that built llvm went on to fail llvm20 with an ld64.lld error Apple's
toolchain cannot produce.  These tests source each script with a fake prefix
whose ``bin/`` holds stub compilers, first on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[2] / "recipes" / "_common"

# These scripts run only on macOS and BSD builders; Windows builds source
# env-windows.ps1.  On the windows-latest runners, `bash -c '. <script>'` exits 1
# before the script runs.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash"),
    reason="the env scripts are bash, for macOS and the BSDs",
)

_STUBS = ("clang", "clang++", "gcc", "g++", "cc", "c++")


def _stub(path: Path, body: str = "exit 0") -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _source(
    tmp_path: Path, script: str, *, xcrun: str | None = None, **extra: str
) -> tuple[str, str]:
    """Source *script* with a cvcpkg prefix holding stub compilers first on
    PATH; return the CC and CXX it exports."""
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    for name in _STUBS:
        _stub(prefix / "bin" / name)
    path = [str(prefix / "bin")]
    if xcrun is not None:
        tools = tmp_path / "xcode-bin"
        tools.mkdir()
        _stub(tools / "xcrun", xcrun)
        path.append(str(tools))
    path.append(os.environ.get("PATH", "/usr/bin:/bin"))

    env = {k: v for k, v in os.environ.items() if k not in ("CC", "CXX")}
    env.update(
        PATH=os.pathsep.join(path),
        CVC_DEPS_PREFIX=str(prefix),
        CVC_INSTALL_DIR=str(prefix),
        CVC_SOURCE_DIR=str(tmp_path / "src"),
        CVC_BUILD_DIR=str(tmp_path / "build"),
        CVC_JOBS="1",
        **extra,
    )
    out = subprocess.run(
        ["bash", "-c", f'. "{COMMON / script}" >/dev/null; printf "%s\\n%s\\n" "$CC" "$CXX"'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    cc, cxx = out.stdout.splitlines()[-2:]
    return cc, cxx


def test_macos_uses_the_compiler_xcrun_reports(tmp_path):
    xcode = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin"
    cc, cxx = _source(tmp_path, "env-macos.sh", xcrun=f'[ "$1" = -f ] && echo "{xcode}/$2"')
    assert (cc, cxx) == (f"{xcode}/clang", f"{xcode}/clang++")


def test_macos_falls_back_to_the_usr_bin_shims(tmp_path):
    # No developer directory: xcrun fails.  /usr/bin/clang is the shim that
    # ships with macOS -- still Apple's, never the prefix's.
    cc, cxx = _source(tmp_path, "env-macos.sh", xcrun="exit 1")
    assert (cc, cxx) == ("/usr/bin/clang", "/usr/bin/clang++")


@pytest.mark.parametrize("script", ["env-freebsd.sh", "env-openbsd.sh"])
def test_bsd_base_clang(tmp_path, script):
    assert _source(tmp_path, script) == ("/usr/bin/clang", "/usr/bin/clang++")


def test_netbsd_prefers_pkgsrc_clang_else_base_gcc(tmp_path):
    # Which branch runs depends on the host; neither is the prefix's stub.
    pkgsrc = all(os.access(f"/usr/pkg/bin/{t}", os.X_OK) for t in ("clang", "clang++"))
    expected = (
        ("/usr/pkg/bin/clang", "/usr/pkg/bin/clang++")
        if pkgsrc
        else ("/usr/bin/gcc", "/usr/bin/g++")
    )
    assert _source(tmp_path, "env-netbsd.sh") == expected


@pytest.mark.parametrize(
    "script", ["env-macos.sh", "env-freebsd.sh", "env-openbsd.sh", "env-netbsd.sh"]
)
def test_an_explicit_compiler_still_wins(tmp_path, script):
    cc, cxx = _source(tmp_path, script, xcrun="exit 1", CC="/opt/cc", CXX="/opt/c++")
    assert (cc, cxx) == ("/opt/cc", "/opt/c++")
