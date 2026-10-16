"""env-windows.ps1 puts MSVC's link.exe ahead of any other link.exe on PATH.

Git for Windows' bin\\bash.exe puts its usr\\bin, which holds coreutils'
link.exe, at the front of PATH.  A build started from Git Bash (every
``shell: bash`` step on GitHub Actions) kept that order, because
Import-CvcMsvcEnv does nothing when cl.exe is already on PATH, and meson
stopped in project() with "Found GNU link.exe instead of MSVC link.exe".
These tests dot-source the script with stub cl.exe and link.exe files and
check the PATH it leaves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ENV_WINDOWS = Path(__file__).resolve().parents[2] / "recipes" / "_common" / "env-windows.ps1"

# PowerShell 7, as the builds use.  Windows PowerShell 5.1 reads the script,
# which has no BOM, as ANSI and fails to parse its non-ASCII comments.
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or _PWSH is None,
    reason="env-windows.ps1 runs only on Windows, under pwsh",
)


def _dir(tmp_path: Path, name: str, *exes: str) -> str:
    d = tmp_path / name
    d.mkdir(parents=True)
    for exe in exes:
        (d / exe).write_bytes(b"")
    return str(d)


def _path_after_load(tmp_path: Path, path: list[str]) -> list[str]:
    """Dot-source env-windows.ps1 with *path* as PATH; return the PATH it leaves."""
    system32 = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("CVC_MSVC_ROOT", "PATH")}
    env.update(
        PATH=";".join(path + [system32]),
        CVC_INSTALL_DIR=str(tmp_path / "install"),
        CVC_SOURCE_DIR=str(tmp_path / "src"),
        CVC_BUILD_DIR=str(tmp_path / "build"),
        CVC_JOBS="1",
    )
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"try {{ . '{ENV_WINDOWS}' *> $null }} catch {{ [Console]::Error.WriteLine($_); exit 3 }}; "
        "[Console]::Out.WriteLine('PATH=' + $env:PATH)"
    )
    out = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(ln for ln in out.stdout.splitlines() if ln.startswith("PATH="))
    # pwsh puts its own directory ($PSHOME) at the front of PATH when it starts.
    pshome = str(Path(_PWSH).resolve().parent).rstrip("\\").lower()
    dirs = [d for d in line[len("PATH=") :].split(";") if d.rstrip("\\").lower() != pshome]
    assert dirs[-1] == system32
    return dirs[:-1]


def test_msvc_bin_moves_ahead_of_git_usr_bin(tmp_path):
    cvc = _dir(tmp_path, "prefix/bin")
    git = _dir(tmp_path, "Git/usr/bin", "link.exe")
    other = _dir(tmp_path, "other")
    msvc = _dir(tmp_path, "MSVC/bin/HostX64/x64", "cl.exe", "link.exe")
    assert _path_after_load(tmp_path, [cvc, git, other, msvc]) == [cvc, msvc, git, other]


def test_path_is_unchanged_when_msvc_link_already_wins(tmp_path):
    cvc = _dir(tmp_path, "prefix/bin")
    msvc = _dir(tmp_path, "MSVC/bin/HostX64/x64", "cl.exe", "link.exe")
    git = _dir(tmp_path, "Git/usr/bin", "link.exe")
    assert _path_after_load(tmp_path, [cvc, msvc, git]) == [cvc, msvc, git]


def test_cl_dir_without_link_exe_is_left_alone(tmp_path):
    git = _dir(tmp_path, "Git/usr/bin", "link.exe")
    cl_only = _dir(tmp_path, "cl-only", "cl.exe")
    assert _path_after_load(tmp_path, [git, cl_only]) == [git, cl_only]


def test_odd_path_entries_do_not_break_the_load(tmp_path):
    git = _dir(tmp_path, "Git/usr/bin", "link.exe")
    msvc = _dir(tmp_path, "MSVC/bin/HostX64/x64", "cl.exe", "link.exe")
    odd = '"C:\\quoted dir"'
    # The trailing backslash still matches cl.exe's directory, and only one copy stays.
    assert _path_after_load(tmp_path, [odd, git, msvc + "\\"]) == [odd, msvc, git]
