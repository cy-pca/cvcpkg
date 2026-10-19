"""env-windows.ps1's Invoke-CvcRewriteInstallPaths relocates deps-prefix paths too.

A CMake export can bake an absolute path to a dependency that lived under
$env:CVC_DEPS_PREFIX at build time: classic FindZLIB returns ${ZLIB_LIBRARIES}
(an absolute .../lib/z.lib), not ZLIB::ZLIB.  assimp 6.0.5+cvc.6's Windows
bundle shipped 'C:/Users/tfx/cvcpkg-builder/.../cvcpkg-prefix-assimp-*/lib/z.lib'
in assimpTargets.cmake, and every consumer's link failed.  The Unix helper
(rewrite-install-paths.sh) has relocated those since assimp cvc.2; these tests
dot-source env-windows.ps1 and check the Windows one does too.
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

ANCHOR = "${CMAKE_CURRENT_LIST_DIR}/../../.."


def _rewrite(tmp_path: Path, deps: Path | None) -> None:
    """Dot-source env-windows.ps1 and run Invoke-CvcRewriteInstallPaths."""
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
        "Invoke-CvcRewriteInstallPaths *> $null"
    )
    subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _targets(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "install" / "lib" / "cmake" / "foo" / "fooTargets.cmake"
    f.parent.mkdir(parents=True)
    f.write_text(text, encoding="utf-8")
    return f


def test_a_deps_prefix_library_path_is_relocated(tmp_path):
    deps = tmp_path / "cvcpkg-prefix-foo"
    fwd = str(deps).replace("\\", "/")
    f = _targets(tmp_path, f'INTERFACE_LINK_LIBRARIES "{fwd}/lib/z.lib;{deps}\\lib\\bz2.lib"\n')
    _rewrite(tmp_path, deps)
    text = f.read_text(encoding="utf-8")
    assert f"{ANCHOR}/lib/z.lib" in text
    assert f"{ANCHOR}\\lib\\bz2.lib" in text
    assert fwd.lower() not in text.lower() and str(deps).lower() not in text.lower()


def test_install_dir_paths_are_still_relocated_first(tmp_path):
    # The deps prefix INSIDE the install dir: the install-dir form is the more specific
    # match and must win, as on Unix.
    install_fwd = str(tmp_path / "install").replace("\\", "/")
    deps = tmp_path / "install" / "deps"
    f = _targets(tmp_path, f'set(_IMPORT_PREFIX "{install_fwd}")\n')
    _rewrite(tmp_path, deps)
    assert f.read_text(encoding="utf-8") == f'set(_IMPORT_PREFIX "{ANCHOR}")\n'


def test_without_a_deps_prefix_nothing_else_changes(tmp_path):
    other = str(tmp_path / "elsewhere").replace("\\", "/")
    body = f'INTERFACE_LINK_LIBRARIES "{other}/lib/z.lib"\n'
    f = _targets(tmp_path, body)
    _rewrite(tmp_path, None)
    assert f.read_text(encoding="utf-8") == body
