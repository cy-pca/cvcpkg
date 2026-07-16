"""Tests for the cpkg (getcpkg.net) integration — prefix scan, serialisers, CLI."""

from __future__ import annotations

import json

import pytest

from cvcpkg import cpkg


def _make_prefix(root):
    """Build a realistic installed-prefix layout under *root*."""
    (root / "include").mkdir(parents=True)
    (root / "include" / "boost").mkdir()
    (root / "lib").mkdir()
    (root / "lib" / "pkgconfig").mkdir()
    (root / "lib" / "cmake").mkdir()
    (root / "bin").mkdir()
    # Library files (linux + windows + macos flavours + a versioned SO)
    (root / "lib" / "libboost_system.so.1.83.0").write_text("")
    (root / "lib" / "libz.a").write_text("")
    (root / "lib" / "hdf5.lib").write_text("")
    (root / "lib" / "libpng.dylib").write_text("")
    (root / "lib" / "pkgconfig" / "zlib.pc").write_text("")
    return root


class TestScanPrefix:
    def test_scan_full_layout(self, tmp_path):
        prefix = _make_prefix(tmp_path / "deps")
        info = cpkg.scan_prefix(prefix)
        assert info.prefix == str(prefix)
        assert str(prefix / "include") in info.include_dirs
        assert str(prefix / "lib") in info.lib_dirs
        assert str(prefix / "lib" / "pkgconfig") in info.pkgconfig_dirs
        assert str(prefix / "lib" / "cmake") in info.cmake_dirs
        assert info.bin_dir == str(prefix / "bin")
        # Link names: lib prefix stripped, versioned SO handled, .lib import lib.
        assert set(info.libs) == {"boost_system", "z", "hdf5", "png"}

    def test_scan_empty_prefix(self, tmp_path):
        prefix = tmp_path / "empty"
        prefix.mkdir()
        info = cpkg.scan_prefix(prefix)
        assert info.include_dirs == []
        assert info.lib_dirs == []
        assert info.libs == []
        assert info.bin_dir == ""

    def test_lib_link_name_variants(self):
        assert cpkg._lib_link_name("libboost_system.so.1.83.0") == "boost_system"
        assert cpkg._lib_link_name("libz.a") == "z"
        assert cpkg._lib_link_name("zlib.lib") == "zlib"
        assert cpkg._lib_link_name("libpng16.dylib") == "png16"
        assert cpkg._lib_link_name("README.txt") is None
        assert cpkg._lib_link_name("boost_headers.cmake") is None

    def test_lib64_is_scanned(self, tmp_path):
        prefix = tmp_path / "deps"
        (prefix / "lib64").mkdir(parents=True)
        (prefix / "lib64" / "libfoo.so").write_text("")
        info = cpkg.scan_prefix(prefix)
        assert str(prefix / "lib64") in info.lib_dirs
        assert "foo" in info.libs


class TestSerialisers:
    def test_lua_roundtrips_structurally(self, tmp_path):
        prefix = _make_prefix(tmp_path / "deps")
        info = cpkg.scan_prefix(prefix)
        lua = cpkg.to_lua(info)
        assert lua.startswith("return {")
        assert "prefix =" in lua
        assert "include_dirs =" in lua
        assert "boost_system" in lua
        # Backslashes (Windows paths) are escaped so load() would parse.
        assert "\\\\" in cpkg.to_lua(cpkg.PrefixInfo(prefix="C:\\a\\b"))

    def test_lua_quotes_are_escaped(self):
        info = cpkg.PrefixInfo(prefix='weird"quote')
        assert '\\"' in cpkg.to_lua(info)

    def test_json_is_valid(self, tmp_path):
        prefix = _make_prefix(tmp_path / "deps")
        info = cpkg.scan_prefix(prefix)
        data = json.loads(cpkg.to_json(info))
        assert data["prefix"] == str(prefix)
        assert "boost_system" in data["libs"]
        assert set(data) == {
            "prefix",
            "include_dirs",
            "lib_dirs",
            "libs",
            "pkgconfig_dirs",
            "cmake_dirs",
            "bin_dir",
        }


# ── CLI (--no-install scan-only path; no network/subprocess) ────

click_testing = pytest.importorskip("click.testing")
from click.testing import CliRunner  # noqa: E402

from cvcpkg.cli import cli  # noqa: E402


class TestCpkgDepsCLI:
    def test_scan_only_lua(self, tmp_path):
        prefix = _make_prefix(tmp_path / "deps")
        res = CliRunner().invoke(
            cli, ["cpkg", "deps", "--prefix", str(prefix), "--no-install", "--format", "lua"]
        )
        assert res.exit_code == 0, res.output
        assert res.output.startswith("return {")
        assert "boost_system" in res.output

    def test_scan_only_json(self, tmp_path):
        prefix = _make_prefix(tmp_path / "deps")
        res = CliRunner().invoke(
            cli, ["cpkg", "deps", "--prefix", str(prefix), "--no-install", "--format", "json"]
        )
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)
        assert "z" in data["libs"]

    def test_missing_prefix_errors(self, tmp_path):
        res = CliRunner().invoke(
            cli, ["cpkg", "deps", "--prefix", str(tmp_path / "nope"), "--no-install"]
        )
        assert res.exit_code != 0
        assert "prefix does not exist" in res.output

    def test_install_with_no_components_errors(self, tmp_path):
        # Without --no-install and no components, it must refuse (not try to install).
        res = CliRunner().invoke(cli, ["cpkg", "deps", "--prefix", str(tmp_path / "d")])
        assert res.exit_code != 0
        assert "no components" in res.output
