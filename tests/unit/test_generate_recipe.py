# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for ``cvcpkg generate`` — recipe generation from an existing project."""

from __future__ import annotations

import pytest

from cvcpkg.cli._generate import (
    _pkgconfig_modules,
    detect_build_system,
    map_dependencies,
    parse_autotools,
    parse_cmake,
    parse_make,
    parse_meson,
    parse_python,
)

# ── Build-system detection ──────────────────────────────────────


class TestDetection:
    @pytest.mark.parametrize(
        ("marker", "expected"),
        [
            ("CMakeLists.txt", "cmake"),
            ("meson.build", "meson"),
            ("configure.ac", "autotools"),
            ("configure", "autotools"),
            ("Makefile", "make"),
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
        ],
    )
    def test_detects_each_system(self, tmp_path, marker, expected):
        (tmp_path / marker).write_text("", encoding="utf-8")
        assert detect_build_system(tmp_path) == expected

    def test_unrecognised_project(self, tmp_path):
        (tmp_path / "README.md").write_text("hi", encoding="utf-8")
        assert detect_build_system(tmp_path) is None

    def test_python_wins_over_cmake(self, tmp_path):
        """A scikit-build project has both; pip drives CMake, not the reverse.

        Choosing cmake here would build the extension but never install the
        importable package.
        """
        (tmp_path / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        assert detect_build_system(tmp_path) == "python"


# ── pkg-config module lists ─────────────────────────────────────


class TestPkgConfigModules:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("zlib", ["zlib"]),
            # The bug this function exists for: a naive split makes "1.2" a package.
            ("zlib >= 1.2 libpng", ["zlib", "libpng"]),
            ("zlib>=1.2, libpng", ["zlib", "libpng"]),
            ("gtk+-3.0 >= 3.22", ["gtk+-3.0"]),
            ("a = 1 b != 2 c < 3", ["a", "b", "c"]),
            ("$UNSET_VAR zlib", ["zlib"]),
            ("", []),
        ],
    )
    def test_parses_version_constraints(self, spec, expected):
        assert _pkgconfig_modules(spec) == expected


# ── Metadata parsers ────────────────────────────────────────────


class TestCMake:
    def test_reads_project_metadata(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(WidgetLib\n  VERSION 2.4.1\n"
            '  DESCRIPTION "A widget library"\n'
            '  HOMEPAGE_URL "https://widgets.example"\n  LANGUAGES CXX)\n'
            "find_package(ZLIB REQUIRED)\nfind_package(Boost REQUIRED)\n",
            encoding="utf-8",
        )
        info = parse_cmake(tmp_path)
        assert info.name == "WidgetLib"
        assert info.version == "2.4.1"
        assert info.description == "A widget library"
        assert info.homepage == "https://widgets.example"
        assert info.deps == ["ZLIB", "Boost"]

    def test_ignores_commented_find_package(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(x VERSION 1.0)\n# find_package(Ghost REQUIRED)\n", encoding="utf-8"
        )
        assert parse_cmake(tmp_path).deps == []

    def test_variable_name_is_not_taken_literally(self, tmp_path):
        """project(${PROJ}) tells us nothing — don't write '${PROJ}' into a recipe."""
        (tmp_path / "CMakeLists.txt").write_text("project(${PROJ} VERSION 1.0)\n", encoding="utf-8")
        info = parse_cmake(tmp_path)
        assert info.name == ""
        assert info.warnings

    def test_skips_toolchain_pseudo_packages(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(x)\nfind_package(Threads REQUIRED)\nfind_package(Python3 REQUIRED)\n",
            encoding="utf-8",
        )
        assert parse_cmake(tmp_path).deps == []


class TestAutotools:
    def test_reads_ac_init(self, tmp_path):
        (tmp_path / "configure.ac").write_text(
            "AC_INIT([libfoo], [1.8.2], [bugs@foo.example], [libfoo], [https://foo.example])\n"
            "PKG_CHECK_MODULES([DEPS], [zlib >= 1.2 libpng])\n"
            "AC_CHECK_LIB([curl], [curl_easy_init])\n",
            encoding="utf-8",
        )
        info = parse_autotools(tmp_path)
        assert info.name == "libfoo"
        assert info.version == "1.8.2"
        assert info.homepage == "https://foo.example"
        assert info.deps == ["zlib", "libpng", "curl"]

    def test_falls_back_to_generated_configure(self, tmp_path):
        (tmp_path / "configure").write_text(
            "#! /bin/sh\nPACKAGE_NAME='libbar'\nPACKAGE_VERSION='3.0'\n", encoding="utf-8"
        )
        info = parse_autotools(tmp_path)
        assert (info.name, info.version) == ("libbar", "3.0")


class TestMeson:
    def test_reads_project(self, tmp_path):
        (tmp_path / "meson.build").write_text(
            "project('mesonproj', 'c', version: '3.1.0', license: 'MIT')\n"
            "zdep = dependency('zlib')\n",
            encoding="utf-8",
        )
        info = parse_meson(tmp_path)
        assert (info.name, info.version, info.license) == ("mesonproj", "3.1.0", "MIT")
        assert info.deps == ["zlib"]


class TestPython:
    def test_pep621(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "cool_tool"\nversion = "0.9.3"\n'
            'description = "Does cool things"\nlicense = { text = "Apache-2.0" }\n'
            'dependencies = ["numpy>=1.20", "requests"]\n'
            '[project.urls]\nHomepage = "https://cool.example"\n',
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert info.name == "cool_tool"
        assert info.version == "0.9.3"
        assert info.license == "Apache-2.0"
        assert info.homepage == "https://cool.example"
        assert info.deps == ["numpy", "requests"]

    def test_poetry(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "potool"\nversion = "1.1"\n'
            'description = "d"\nhomepage = "https://p.example"\n'
            '[tool.poetry.dependencies]\npython = "^3.11"\nclick = "^8"\n',
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert (info.name, info.version) == ("potool", "1.1")
        # 'python' is the interpreter constraint, not a package dependency.
        assert info.deps == ["click"]

    def test_dynamic_version_is_flagged(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "dyn"\ndynamic = ["version"]\n', encoding="utf-8"
        )
        info = parse_python(tmp_path)
        assert info.version == ""
        assert any("dynamic" in w for w in info.warnings)

    def test_setup_py_is_read_not_executed(self, tmp_path):
        """Running a stranger's setup.py to read its name would be reckless."""
        (tmp_path / "setup.py").write_text(
            "import os\nos.system('touch pwned')\n" "setup(name='legacy', version='0.1')\n",
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert (info.name, info.version) == ("legacy", "0.1")
        assert not (tmp_path / "pwned").exists()


class TestMakefile:
    def test_reads_what_little_there_is(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            "PACKAGE = tinytool\nVERSION = 0.4\nall:\n\tcc -o tinytool main.c\n", encoding="utf-8"
        )
        info = parse_make(tmp_path)
        assert (info.name, info.version) == ("tinytool", "0.4")
        # A plain Makefile's install step is always a guess; say so.
        assert info.warnings


# ── Dependency mapping ──────────────────────────────────────────


class TestDependencyMapping:
    KNOWN = {"zlib", "boost", "hdf5", "libpng", "openssl"}

    def test_lowercases_to_match(self):
        resolved, unresolved = map_dependencies(["ZLIB", "Boost"], self.KNOWN)
        assert resolved == ["zlib", "boost"]
        assert unresolved == []

    def test_applies_aliases(self):
        resolved, _ = map_dependencies(["PNG", "libcrypto"], self.KNOWN)
        assert resolved == ["libpng", "openssl"]

    def test_unknown_stays_a_comment(self):
        """A dep that is not a real recipe must never reach depends:.

        cvcpkg validate rejects a recipe naming a dependency it cannot
        resolve, so emitting a guess produces output that fails on the
        very next command the user runs.
        """
        resolved, unresolved = map_dependencies(["ZLIB", "SuperRareThing"], self.KNOWN)
        assert resolved == ["zlib"]
        assert unresolved == ["SuperRareThing"]

    def test_deduplicates(self):
        resolved, _ = map_dependencies(["ZLIB", "zlib", "z"], self.KNOWN)
        assert resolved == ["zlib"]

    def test_no_recipe_set_suggests_rather_than_asserts(self):
        """With nothing to check against, everything is a suggestion."""
        resolved, unresolved = map_dependencies(["ZLIB"], set())
        assert resolved == []
        assert unresolved == ["ZLIB -> zlib?"]
