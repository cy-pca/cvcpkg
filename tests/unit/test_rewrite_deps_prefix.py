"""cvc_rewrite_install_paths must relocate DEPS-prefix absolute paths, not just
the install prefix.

A recipe's exported CMake target can bake an absolute path to a DEPENDENCY that
lived under ``$CVC_DEPS_PREFIX`` at build time. The canonical case: assimp built
with ``-DASSIMP_BUILD_ZLIB=OFF`` links ``${ZLIB_LIBRARIES}`` -- an absolute path
emitted by the classic FindZLIB module -- and CMake exports THAT into
``assimpTargets.cmake`` instead of the relocatable ``ZLIB::ZLIB`` target. The
path (``/tmp/cvcpkg-builder/.../lib/libz.so``) exists on no consumer machine, so
any downstream ``find_package(assimp CONFIG)`` + ``target_link_libraries(...
assimp::assimp)`` fails to link everywhere (locally and in CI).

CMake relocates its OWN paths via ``${_IMPORT_PREFIX}``; the leak is the external
dependency absolute path it does not touch. The bash relocatability helper
(``recipes/_common/rewrite-install-paths.sh``, run automatically by
``cvc_cmake_build``) therefore rewrites ``$CVC_DEPS_PREFIX`` paths to the same
per-file ``${CMAKE_CURRENT_LIST_DIR}`` anchor it uses for ``$CVC_INSTALL_DIR`` --
sound because in cvcpkg's FLAT install every dependency co-locates with the
package at the consumer prefix.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# cvc_rewrite_install_paths is a POSIX build-time shell helper sourced by
# cvc_cmake_build (env-{linux,macos,freebsd,openbsd,netbsd}.sh); windows recipes
# use build.ps1 and never touch it. Skip where it (or bash) does not apply.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX-only build-time helper (bash); windows recipes use build.ps1",
)

HELPER = Path(__file__).resolve().parents[2] / "recipes" / "_common" / "rewrite-install-paths.sh"


def _run_rewrite(install_dir: Path, deps_prefix: Path) -> None:
    subprocess.run(
        ["bash", "-c", f'source "{HELPER}"; cvc_rewrite_install_paths'],
        env={
            **os.environ,
            "CVC_INSTALL_DIR": str(install_dir),
            "CVC_DEPS_PREFIX": str(deps_prefix),
            "CVC_PLATFORM": "linux",
        },
        check=True,
    )


def test_deps_prefix_absolute_in_cmake_export_is_relocated(tmp_path):
    install = tmp_path / "install"
    deps = tmp_path / "depsprefix"
    cmake_dir = install / "lib" / "cmake" / "assimp-6.0"
    cmake_dir.mkdir(parents=True)
    export = cmake_dir / "assimpTargets.cmake"
    # assimp's exact broken shape: a per-config genex wrapping the absolute
    # $CVC_DEPS_PREFIX zlib path, plus assimp's own $CVC_INSTALL_DIR .so.
    export.write_text(
        "set_target_properties(assimp::assimp PROPERTIES\n"
        "  INTERFACE_LINK_LIBRARIES "
        f'"$<$<NOT:$<CONFIG:DEBUG>>:{deps}/lib/libz.so>;'
        f'$<$<CONFIG:DEBUG>:{deps}/lib/libz.so>;rt"\n'
        f'  IMPORTED_LOCATION_RELEASE "{install}/lib/libassimp.so.6.0.5"\n'
        ")\n",
        encoding="utf-8",
    )

    _run_rewrite(install, deps)

    text = export.read_text(encoding="utf-8")
    # No absolute build-sandbox path of EITHER prefix survives.
    assert str(deps) not in text, "the deps-prefix zlib path was not relocated"
    assert str(install) not in text, "the install-prefix path was not relocated"
    # The relocatable anchor points at the flat prefix root (3 levels up from
    # lib/cmake/assimp-6.0), where the co-installed zlib lives.
    assert "${CMAKE_CURRENT_LIST_DIR}/../../../lib/libz.so" in text
    assert "${CMAKE_CURRENT_LIST_DIR}/../../../lib/libassimp.so.6.0.5" in text
    # The generator-expression structure and the sibling 'rt' entry are preserved.
    assert "$<$<NOT:$<CONFIG:DEBUG>>:" in text
    assert ";rt" in text


def test_clean_export_is_left_untouched(tmp_path):
    """No CVC_DEPS_PREFIX / CVC_INSTALL_DIR path present -> the helper is a no-op."""
    install = tmp_path / "install"
    deps = tmp_path / "depsprefix"
    cmake_dir = install / "lib" / "cmake" / "foo"
    cmake_dir.mkdir(parents=True)
    export = cmake_dir / "fooTargets.cmake"
    original = (
        "set_target_properties(foo::foo PROPERTIES\n"
        '  INTERFACE_LINK_LIBRARIES "ZLIB::ZLIB;rt"\n'
        '  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libfoo.so"\n'
        ")\n"
    )
    export.write_text(original, encoding="utf-8")

    _run_rewrite(install, deps)

    assert export.read_text(encoding="utf-8") == original
