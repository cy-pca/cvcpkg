"""env-windows.ps1's Assert-CvcNoBuildPrefixPaths fails a build that ships a build path.

Invoke-CvcRewriteInstallPaths relocates .pc and .cmake files only, so scripts
kept the build's --prefix: glib 2.80.5+cvc.4's bin/gdbus-codegen had
'C:/Users/jmr/cvcpkg-builder/.../install/share', and openssl 3.4.1+cvc.10's
bin/c_rehash.pl has the same directory as a Perl string,
"C:\\\\Users\\\\jmr\\\\cvcpkg-builder\\\\...\\\\install".  These tests dot-source
env-windows.ps1 and check that every spelling of the install and deps prefixes
fails the check, and that binaries and clean trees pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ENV_WINDOWS = Path(__file__).resolve().parents[2] / "recipes" / "_common" / "env-windows.ps1"

# PowerShell 7, as the builds use (see test_env_windows_linker.py).
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or _PWSH is None,
    reason="env-windows.ps1 runs only on Windows, under pwsh",
)


def _check(
    tmp_path: Path, deps: Path | None = None, extra: str = ""
) -> subprocess.CompletedProcess[str]:
    """Dot-source env-windows.ps1 and run Assert-CvcNoBuildPrefixPaths 'demo' [extra args]."""
    env = {
        k: v for k, v in os.environ.items() if k.upper() not in ("CVC_MSVC_ROOT", "CVC_DEPS_PREFIX")
    }
    env.update(
        CVC_INSTALL_DIR=str(tmp_path / "install"),
        CVC_SOURCE_DIR=str(tmp_path / "src"),
        CVC_BUILD_DIR=str(tmp_path / "build"),
        CVC_JOBS="1",
    )
    if deps is not None:
        env["CVC_DEPS_PREFIX"] = str(deps)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"try {{ . '{ENV_WINDOWS}' *> $null }} catch {{ [Console]::Error.WriteLine($_); exit 3 }}; "
        f"try {{ Assert-CvcNoBuildPrefixPaths 'demo' {extra} }} "
        "catch { [Console]::Error.WriteLine($_); exit 1 }"
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
    )


def _install_file(tmp_path: Path, rel: str, data: str | bytes) -> Path:
    f = tmp_path / "install" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        f.write_bytes(data)
    else:
        f.write_text(data, encoding="utf-8")
    return f


def _fails_naming(result: subprocess.CompletedProcess[str], rel: str) -> None:
    assert result.returncode == 1, result.stderr
    assert "demo: installed files still name" in result.stderr
    assert rel.replace("/", "\\") in result.stdout


def test_a_perl_string_install_dir_fails(tmp_path):
    # c_rehash.pl's shape: the prefix inside a double-quoted Perl string.
    doubled = str(tmp_path / "install").replace("\\", "\\\\")
    _install_file(tmp_path, "bin/c_rehash.pl", f'my $prefix = "{doubled}";\n')
    _fails_naming(_check(tmp_path), "bin/c_rehash.pl")


def test_a_forward_slash_install_dir_fails(tmp_path):
    # gdbus-codegen's shape: meson writes the datadir with forward slashes.
    fwd = str(tmp_path / "install").replace("\\", "/")
    _install_file(
        tmp_path, "bin/gdbus-codegen", f"path = os.path.join('{fwd}/share', 'glib-2.0')\n"
    )
    _fails_naming(_check(tmp_path), "bin/gdbus-codegen")


def test_a_deps_prefix_path_fails_in_any_case(tmp_path):
    deps = tmp_path / "cvcpkg-prefix-demo"
    _install_file(tmp_path, "share/demo/demo.conf", f"libdir={str(deps).upper()}\\lib\n")
    _fails_naming(_check(tmp_path, deps), "share/demo/demo.conf")


def test_an_msys_install_dir_fails(tmp_path):
    root = str(tmp_path / "install")
    msys = "/" + root[0].lower() + root[2:].replace("\\", "/")
    _install_file(tmp_path, "bin/demo-config", f"prefix={msys}\n")
    _fails_naming(_check(tmp_path), "bin/demo-config")


def test_binaries_and_clean_files_pass(tmp_path):
    # A DLL may embed the path (openssl's OPENSSLDIR); no text rewrite can fix it.
    _install_file(
        tmp_path, "bin/demo.dll", b"MZ\x00" + str(tmp_path / "install").encode() + b"\x00"
    )
    _install_file(
        tmp_path, "bin/c_rehash.pl", 'use FindBin ();\nmy $prefix = "$FindBin::RealBin/..";\n'
    )
    result = _check(tmp_path, tmp_path / "cvcpkg-prefix-demo")
    assert result.returncode == 0, result.stderr


def test_extra_prefixes_are_checked_too(tmp_path):
    # openssl's shape: a DESTDIR staging dir the recipe copies the install from.
    stage = tmp_path / "stage"
    _install_file(
        tmp_path, "ssl/misc/CA.pl", f'my $dir = "{str(stage).replace(chr(92), "/")}/ssl";\n'
    )
    assert _check(tmp_path).returncode == 0
    _fails_naming(_check(tmp_path, extra=f"-ExtraPrefixes '{stage}', ''"), "ssl/misc/CA.pl")
