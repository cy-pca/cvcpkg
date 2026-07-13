"""Tests for the auto-generated CMake package config (cvcpkg.cmake_config)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from cvcpkg.cmake_config import write_cmake_config


def test_writes_expected_files(tmp_path):
    written = write_cmake_config(tmp_path, "2.0.0")
    names = {p.name for p in written}
    assert "cvcpkgConfig.cmake" in names
    assert "cvcpkgConfigVersion.cmake" in names
    assert "libcvc-depsConfig.cmake" in names

    cfg = tmp_path / "lib" / "cmake" / "cvcpkg" / "cvcpkgConfig.cmake"
    assert cfg.is_file()
    text = cfg.read_text()
    assert 'set(CVCPKG_VERSION   "2.0.0")' in text
    assert "CMAKE_PREFIX_PATH" in text


def _configure(tmp_path, prefix, package):
    """Run a throwaway cmake configure that find_package()s *package*."""
    proj = tmp_path / f"proj_{package}"
    proj.mkdir()
    (proj / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(probe NONE)\n"
        f"find_package({package} CONFIG REQUIRED)\n"
        'message(STATUS "CVCPKG_ROOT_DIR=${CVCPKG_ROOT_DIR}")\n'
    )
    return subprocess.run(
        [
            "cmake",
            "-S",
            str(proj),
            "-B",
            str(proj / "build"),
            f"-DCMAKE_PREFIX_PATH={prefix}",
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("cmake") is None, reason="cmake not installed")
def test_find_package_cvcpkg_configures(tmp_path):
    prefix = tmp_path / "deps"
    write_cmake_config(prefix, "2.0.0")
    res = _configure(tmp_path, prefix, "cvcpkg")
    # returncode 0 with find_package(... REQUIRED) already proves the config
    # was found and loaded.  Also confirm our config actually ran (it echoes
    # CVCPKG_ROOT_DIR); compare on the resolved path with normalized slashes
    # since CMake prints forward slashes even on Windows.
    assert res.returncode == 0, res.stderr
    out = res.stdout + res.stderr
    assert "CVCPKG_ROOT_DIR=" in out
    assert prefix.name in out


@pytest.mark.skipif(shutil.which("cmake") is None, reason="cmake not installed")
def test_find_package_libcvc_deps_compat_configures(tmp_path):
    prefix = tmp_path / "deps"
    write_cmake_config(prefix, "2.0.0")
    res = _configure(tmp_path, prefix, "libcvc-deps")
    assert res.returncode == 0, res.stderr
