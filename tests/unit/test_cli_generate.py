# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""End-to-end tests for the ``cvcpkg generate`` command.

``test_generate_recipe.py`` already exercises the pure parser/mapper
helpers.  This module drives the click command itself — detection ->
parse -> dependency mapping -> source detection -> render -> write —
so the command body, the git ``source:`` detection, and the recipe
renderer are all covered.  Every git call is mocked; nothing real is
spawned and no network or default recipe set is consulted.
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest
from click.testing import CliRunner

from cvcpkg.cli import _generate, cli
from cvcpkg.cli._generate import (
    ProjectInfo,
    _git,
    _known_recipe_names,
    _pkgconfig_modules,
    _read,
    detect_source,
    map_dependencies,
    parse_autotools,
    parse_cmake,
    parse_make,
    parse_meson,
    parse_python,
    render_recipe,
)

# tomllib is stdlib only on 3.11+; pyproject parsing is a no-op below that.
requires_tomllib = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="tomllib is stdlib only on 3.11+"
)


@pytest.fixture()
def runner():
    return CliRunner()


def _invoke(runner, project, dest, *extra):
    """Run ``generate`` against *project*, writing recipes under *dest*."""
    return runner.invoke(
        cli,
        [
            "generate",
            str(project),
            "--dir",
            str(dest),
            "--no-default-recipes",
            *extra,
        ],
    )


# ── build-system detection through the command ──────────────────


class TestDetectionErrors:
    def test_unrecognised_project_is_a_click_error(self, runner, tmp_path):
        (tmp_path / "README.md").write_text("hi", encoding="utf-8")
        res = _invoke(runner, tmp_path, tmp_path / "recipes")
        assert res.exit_code != 0
        assert "could not detect a build system" in res.output

    def test_build_system_override_forces_parser(self, runner, tmp_path):
        # A CMake marker is present, but --build-system make forces parse_make,
        # which emits its "install step is a guess" warning.
        (tmp_path / "CMakeLists.txt").write_text("project(x VERSION 1.0)\n", encoding="utf-8")
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--build-system", "make", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "detected a make project" in res.output


class TestNameValidation:
    def test_unusable_derived_name_is_rejected(self, runner, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("project(x VERSION 1.0)\n", encoding="utf-8")
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--name", "9nope")
        assert res.exit_code != 0
        assert "not usable" in res.output

    def test_name_is_normalised(self, runner, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("project(x VERSION 1.0)\n", encoding="utf-8")
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--name", "My_Cool Lib", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "recipes/my-cool-lib/recipe.yaml" in res.output


# ── dry-run rendering per build system ──────────────────────────


class TestDryRun:
    def test_cmake_dry_run_emits_ps1(self, runner, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(WidgetLib VERSION 2.4.1\n"
            '  DESCRIPTION "A widget library"\n'
            '  HOMEPAGE_URL "https://widgets.example")\n'
            "find_package(ZLIB REQUIRED)\n",
            encoding="utf-8",
        )
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        # CMake is windows-capable, so a build.ps1 is rendered too.
        assert "--- recipes/widgetlib/recipe.yaml ---" in res.output
        assert "--- recipes/widgetlib/build.sh ---" in res.output
        assert "--- recipes/widgetlib/build.ps1 ---" in res.output
        assert "Invoke-CvcCMakeBuild" in res.output
        assert 'upstream_version: "2.4.1"' in res.output
        assert "homepage: https://widgets.example" in res.output
        # ZLIB cannot resolve against an empty recipe set, so it is a comment.
        assert "unmatched:" in res.output
        assert "ZLIB -> zlib?" in res.output
        # No files were written in dry-run mode.
        assert not (tmp_path / "recipes").exists()

    def test_meson_dry_run_has_no_windows_row(self, runner, tmp_path):
        (tmp_path / "meson.build").write_text(
            "project('mesonproj', 'c', version: '3.1.0', license: 'MIT')\n" "dependency('zlib')\n",
            encoding="utf-8",
        )
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        # Meson is not windows-capable: no build.ps1, and the explanatory note.
        assert "--- recipes/mesonproj/build.ps1 ---" not in res.output
        assert "No windows entry" in res.output
        assert "meson setup" in res.output
        assert 'license: "MIT"' in res.output

    @requires_tomllib
    def test_python_dry_run_emits_pip_ps1(self, runner, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "cool_tool"\nversion = "0.9.3"\n'
            'description = "Does cool things"\n',
            encoding="utf-8",
        )
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "--- recipes/cool-tool/build.ps1 ---" in res.output
        assert "pip install . --no-deps" in res.output
        assert "version:      0.9.3" in res.output


# ── writing recipes to disk ─────────────────────────────────────


class TestWrite:
    def test_writes_all_three_files_for_cmake(self, runner, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "CMakeLists.txt").write_text("project(demo VERSION 1.0)\n", encoding="utf-8")
        dest = tmp_path / "recipes"
        res = _invoke(runner, proj, dest)
        assert res.exit_code == 0, res.output
        target = dest / "demo"
        assert (target / "recipe.yaml").is_file()
        assert (target / "build.sh").is_file()
        assert (target / "build.ps1").is_file()
        assert "wrote recipe 'demo'" in res.output
        assert "Next steps:" in res.output
        # build.sh keeps LF line endings even on Windows.
        assert b"\r\n" not in (target / "build.sh").read_bytes()

    def test_meson_write_has_no_ps1(self, runner, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "meson.build").write_text("project('m', 'c', version: '1.0')\n", encoding="utf-8")
        dest = tmp_path / "recipes"
        res = _invoke(runner, proj, dest)
        assert res.exit_code == 0, res.output
        assert (dest / "m" / "recipe.yaml").is_file()
        assert not (dest / "m" / "build.ps1").exists()

    def test_existing_target_without_force_errors(self, runner, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "CMakeLists.txt").write_text("project(demo VERSION 1.0)\n", encoding="utf-8")
        dest = tmp_path / "recipes"
        (dest / "demo").mkdir(parents=True)
        res = _invoke(runner, proj, dest)
        assert res.exit_code != 0
        assert "already exists" in res.output

    def test_existing_target_with_force_overwrites(self, runner, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "CMakeLists.txt").write_text("project(demo VERSION 1.0)\n", encoding="utf-8")
        dest = tmp_path / "recipes"
        target = dest / "demo"
        target.mkdir(parents=True)
        (target / "stale.txt").write_text("old", encoding="utf-8")
        res = _invoke(runner, proj, dest, "--force")
        assert res.exit_code == 0, res.output
        assert (target / "recipe.yaml").is_file()


# ── resolved dependencies + warnings surfaced by the command ────


class TestDependencyAndWarningOutput:
    def test_resolved_dependencies_are_reported(self, runner, tmp_path, monkeypatch):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(x VERSION 1.0)\nfind_package(ZLIB REQUIRED)\n"
            "find_package(Weird REQUIRED)\n",
            encoding="utf-8",
        )
        # Control the known recipe set so mapping is deterministic.
        monkeypatch.setattr(_generate, "_known_recipe_names", lambda dirs, nd: {"zlib"})
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "dependencies: zlib" in res.output
        # Weird has no recipe: it is reported as unmatched, not written.
        assert "unmatched:    Weird" in res.output
        assert "- name: zlib" in res.output

    def test_parser_warnings_go_to_stderr(self, runner, tmp_path):
        # A plain Makefile always emits an "install step is a guess" note.
        (tmp_path / "Makefile").write_text("PACKAGE = tool\nVERSION = 0.4\n", encoding="utf-8")
        res = _invoke(runner, tmp_path, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "note:" in res.stderr
        assert "plain Makefile" in res.stderr


# ── git-based source detection (subprocess mocked) ──────────────


def _fake_git_run(url="git@github.com:me/proj.git", commit="abc123", tag="v1.2.3", rc=0):
    def run(cmd, **kw):
        args = cmd[3:]  # cmd == ["git", "-C", proj, *args]
        out = mock.MagicMock()
        out.returncode = rc
        if args[:2] == ["remote", "get-url"]:
            out.stdout = url + "\n"
        elif args[:1] == ["rev-parse"]:
            out.stdout = commit + "\n"
        elif args[:1] == ["describe"]:
            out.stdout = tag + "\n"
        else:
            out.stdout = ""
        return out

    return run


class TestGitSource:
    def test_git_source_block_and_tag_version(self, runner, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        # No VERSION in the CMake file, so the git tag must supply the version.
        (proj / "CMakeLists.txt").write_text("project(gitproj)\n", encoding="utf-8")
        monkeypatch.setattr(_generate.subprocess, "run", _fake_git_run())
        res = _invoke(runner, proj, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "type: git" in res.output
        # scp-style remote is rewritten to an https URL anyone can fetch.
        assert "url: https://github.com/me/proj.git" in res.output
        assert "commit: abc123" in res.output
        # The exact-match tag (v-stripped) fills the empty version.
        assert 'upstream_version: "1.2.3"' in res.output

    def test_git_checkout_without_origin_warns(self, runner, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "CMakeLists.txt").write_text("project(x VERSION 1.0)\n", encoding="utf-8")
        # returncode != 0 for every git call -> no url found.
        monkeypatch.setattr(_generate.subprocess, "run", _fake_git_run(rc=1))
        res = _invoke(runner, proj, tmp_path / "recipes", "--dry-run")
        assert res.exit_code == 0, res.output
        assert "no 'origin' remote" in res.stderr
        # Falls through to the tarball TODO template.
        assert "type: tarball" in res.output


# ── detect_source / _git unit-level branches ────────────────────


class TestDetectSourceUnit:
    def test_non_git_returns_tarball_todo(self, tmp_path):
        lines = detect_source(tmp_path, ProjectInfo())
        assert "source:" in lines
        assert any("type: tarball" in ln for ln in lines)

    def test_https_remote_is_kept_verbatim(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        info = ProjectInfo(version="9.9")  # a version already set: tag must NOT override
        monkeypatch.setattr(
            _generate.subprocess,
            "run",
            _fake_git_run(url="https://example.com/x.git", tag="v2.0"),
        )
        lines = detect_source(tmp_path, info)
        assert "  url: https://example.com/x.git" in lines
        assert "  submodules: false" in lines
        assert info.version == "9.9"

    def test_git_helper_handles_oserror(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise OSError("no git binary")

        monkeypatch.setattr(_generate.subprocess, "run", boom)
        assert _git(tmp_path, "rev-parse", "HEAD") == ""

    def test_git_helper_nonzero_returncode_is_empty(self, tmp_path, monkeypatch):
        out = mock.MagicMock()
        out.returncode = 128
        out.stdout = "fatal: not a repo"
        monkeypatch.setattr(_generate.subprocess, "run", lambda *a, **k: out)
        assert _git(tmp_path, "rev-parse", "HEAD") == ""


# ── render_recipe branch coverage ───────────────────────────────


class TestRenderRecipe:
    def test_full_metadata_with_resolved_deps(self):
        info = ProjectInfo(
            version="1.2",
            description="line one\nline two",
            homepage="https://h.example",
            license="MIT",
        )
        text = render_recipe(
            name="foo",
            system="cmake",
            info=info,
            source_lines=["source:", "  type: git"],
            resolved=["zlib", "boost"],
            unresolved=["mystery"],
        )
        assert "name: foo" in text
        assert 'upstream_version: "1.2"' in text
        assert "homepage: https://h.example" in text
        assert 'license: "MIT"' in text
        # Multi-line description is flattened to one line.
        assert "line one line two" in text
        assert "  build:" in text
        assert "    - name: zlib" in text
        assert "    - name: boost" in text
        # Unresolved deps become commented hints, never real depends entries.
        assert "#   - mystery" in text
        # cmake ships a windows matrix row.
        assert "platform: windows" in text

    def test_missing_metadata_falls_back_to_todos(self):
        info = ProjectInfo()  # everything empty
        text = render_recipe(
            name="bar",
            system="autotools",
            info=info,
            source_lines=["source:", "  type: tarball"],
            resolved=[],
            unresolved=[],
        )
        assert 'upstream_version: "0.0.0"' in text
        assert "# homepage: https://example.com" in text
        assert 'license: "TODO-SPDX"' in text
        assert "TODO one-line description of bar." in text
        # No resolved deps -> an empty build list, no windows row for autotools.
        assert "  build: []" in text
        assert "platform: windows" not in text
        assert "No windows entry" in text


# ── _known_recipe_names ─────────────────────────────────────────


class TestKnownRecipeNames:
    def test_collects_names_and_provides_slots(self, monkeypatch):
        recipes = [
            types.SimpleNamespace(name="zlib", provides=["z", "libz"]),
            types.SimpleNamespace(name="openssl", provides=None),
        ]
        monkeypatch.setattr(_generate, "_resolve_recipes_dirs", lambda dirs, no_default: ["d"])
        monkeypatch.setattr("cvcpkg.builder.list_recipes", lambda d: recipes)
        names = _known_recipe_names((), False)
        assert names == {"zlib", "z", "libz", "openssl"}

    def test_resolve_failure_returns_empty_set(self, monkeypatch):
        def boom(dirs, no_default):
            raise RuntimeError("no recipes dir")

        monkeypatch.setattr(_generate, "_resolve_recipes_dirs", boom)
        assert _known_recipe_names((), False) == set()

    def test_list_recipes_failure_is_skipped(self, monkeypatch):
        def boom(d):
            raise ValueError("corrupt")

        monkeypatch.setattr(_generate, "_resolve_recipes_dirs", lambda dirs, no_default: ["d"])
        monkeypatch.setattr("cvcpkg.builder.list_recipes", boom)
        assert _known_recipe_names((), False) == set()


# ── low-level helpers ───────────────────────────────────────────


class TestReadAndPkgConfig:
    def test_read_swallows_oserror(self, tmp_path):
        # Reading a directory as text raises OSError; _read returns "".
        assert _read(tmp_path) == ""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("zlib", ["zlib"]),
            ("zlib >= 1.2 libpng", ["zlib", "libpng"]),
            ("zlib>=1.2, libpng", ["zlib", "libpng"]),
            ("a = 1 b != 2 c < 3", ["a", "b", "c"]),
            ("$UNSET zlib", ["zlib"]),
            ("", []),
        ],
    )
    def test_pkgconfig_modules(self, spec, expected):
        assert _pkgconfig_modules(spec) == expected


# ── parse_cmake branch coverage ─────────────────────────────────


class TestParseCmake:
    def test_full_metadata_and_pkg_modules(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(WidgetLib VERSION 2.4.1\n"
            '  DESCRIPTION "A widget library"\n'
            '  HOMEPAGE_URL "https://widgets.example")\n'
            "find_package(ZLIB REQUIRED)\n"
            "pkg_check_modules(DEPS REQUIRED IMPORTED_TARGET libpng >= 1.6 freetype2)\n",
            encoding="utf-8",
        )
        info = parse_cmake(tmp_path)
        assert info.name == "WidgetLib"
        assert info.version == "2.4.1"
        assert info.description == "A widget library"
        assert info.homepage == "https://widgets.example"
        assert info.deps == ["ZLIB", "libpng", "freetype2"]

    def test_variable_name_and_version_are_dropped(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(${PROJ} VERSION ${VER})\n", encoding="utf-8"
        )
        info = parse_cmake(tmp_path)
        assert info.name == ""
        assert info.version == ""
        assert info.warnings  # a note explains the variable name was ignored

    def test_toolchain_pseudo_packages_are_skipped(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "project(x)\nfind_package(Threads REQUIRED)\nfind_package(Python3 REQUIRED)\n",
            encoding="utf-8",
        )
        assert parse_cmake(tmp_path).deps == []


# ── parse_autotools branch coverage ─────────────────────────────


class TestParseAutotools:
    def test_ac_init_with_positional_url(self, tmp_path):
        (tmp_path / "configure.ac").write_text(
            "AC_INIT([libfoo], [1.8.2], [bugs@foo], [libfoo], [https://foo.example])\n"
            "PKG_CHECK_MODULES([DEPS], [zlib >= 1.2 libpng])\n"
            "AC_CHECK_LIB([curl], [curl_easy_init])\n",
            encoding="utf-8",
        )
        info = parse_autotools(tmp_path)
        assert (info.name, info.version) == ("libfoo", "1.8.2")
        assert info.homepage == "https://foo.example"
        assert info.deps == ["zlib", "libpng", "curl"]

    def test_ac_init_url_fallback_from_bug_field(self, tmp_path):
        # No positional URL arg; the http-looking bug-report field is used.
        (tmp_path / "configure.ac").write_text(
            "AC_INIT([bar], [2.0], [https://bar.example])\n", encoding="utf-8"
        )
        info = parse_autotools(tmp_path)
        assert info.homepage == "https://bar.example"

    def test_generated_configure_fallback(self, tmp_path):
        (tmp_path / "configure").write_text(
            "#! /bin/sh\nPACKAGE_NAME='libbar'\nPACKAGE_VERSION='3.0'\n"
            "PACKAGE_URL='https://bar.example'\n",
            encoding="utf-8",
        )
        info = parse_autotools(tmp_path)
        assert (info.name, info.version) == ("libbar", "3.0")
        assert info.homepage == "https://bar.example"

    def test_generated_configure_without_name_warns(self, tmp_path):
        (tmp_path / "configure").write_text("#! /bin/sh\necho hi\n", encoding="utf-8")
        info = parse_autotools(tmp_path)
        assert info.name == ""
        assert any("could not read package name" in w for w in info.warnings)


# ── parse_meson false branches ──────────────────────────────────


class TestParseMeson:
    def test_bare_project_has_no_version_or_license(self, tmp_path):
        (tmp_path / "meson.build").write_text("project('m', 'c')\n", encoding="utf-8")
        info = parse_meson(tmp_path)
        assert info.name == "m"
        assert info.version == ""
        assert info.license == ""
        assert info.deps == []


# ── parse_python variants ───────────────────────────────────────


@requires_tomllib
class TestParsePython:
    def test_pep621_license_dict_and_urls(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "rich_tool"\nversion = "1.0"\n'
            'license = { text = "BSD-3-Clause" }\n'
            'dependencies = ["numpy>=1", "click ; python_version>\'3\'"]\n'
            '[project.urls]\nHomepage = "https://rich.example"\n',
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert info.license == "BSD-3-Clause"
        assert info.homepage == "https://rich.example"
        assert info.deps == ["numpy", "click"]

    def test_pep621_license_string_and_unknown_url_key(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "s"\nversion = "1"\nlicense = "MIT"\n'
            '[project.urls]\nDocs = "https://docs.example"\n',
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert info.license == "MIT"
        # No Homepage/Repository/Source key: falls back to the first URL value.
        assert info.homepage == "https://docs.example"

    def test_pep621_dynamic_version_flagged(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "dyn"\ndynamic = ["version"]\n', encoding="utf-8"
        )
        info = parse_python(tmp_path)
        assert info.version == ""
        assert any("dynamic" in w for w in info.warnings)

    def test_poetry(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "potool"\nversion = "1.1"\n'
            'description = "d"\nlicense = "MIT"\nhomepage = "https://p.example"\n'
            '[tool.poetry.dependencies]\npython = "^3.11"\nclick = "^8"\n',
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert (info.name, info.version, info.license) == ("potool", "1.1", "MIT")
        assert info.deps == ["click"]  # the python constraint is not a dependency

    def test_malformed_pyproject_warns_and_falls_through(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project\nname = broken", encoding="utf-8")
        info = parse_python(tmp_path)
        assert info.name == ""
        assert any("could not parse pyproject.toml" in w for w in info.warnings)

    def test_pyproject_without_project_or_poetry_table(self, tmp_path):
        # A valid TOML with no [project] and no [tool.poetry]: nothing to read,
        # and (no setup.cfg/setup.py either) an empty ProjectInfo comes back.
        (tmp_path / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools"]\n', encoding="utf-8"
        )
        info = parse_python(tmp_path)
        assert info.name == ""
        assert info.deps == []

    def test_setup_cfg(self, tmp_path):
        (tmp_path / "setup.cfg").write_text(
            "[metadata]\nname = cfgtool\nversion = 2.0\ndescription = d\n"
            "license = MIT\nurl = https://cfg.example\n"
            "[options]\ninstall_requires =\n    numpy>=1\n    requests\n",
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert info.name == "cfgtool"
        assert info.version == "2.0"
        assert info.homepage == "https://cfg.example"
        assert info.deps == ["numpy", "requests"]

    def test_setup_py_is_read_statically(self, tmp_path):
        (tmp_path / "setup.py").write_text(
            "import os\nos.system('touch pwned')\nsetup(name='legacy', version='0.1')\n",
            encoding="utf-8",
        )
        info = parse_python(tmp_path)
        assert (info.name, info.version) == ("legacy", "0.1")
        assert not (tmp_path / "pwned").exists()  # setup.py must never be executed
        assert any("not executed" in w for w in info.warnings)


# ── parse_make false branches ───────────────────────────────────


class TestParseMake:
    def test_reads_name_and_version(self, tmp_path):
        (tmp_path / "Makefile").write_text("PACKAGE = tinytool\nVERSION = 0.4\n", encoding="utf-8")
        info = parse_make(tmp_path)
        assert (info.name, info.version) == ("tinytool", "0.4")
        assert info.warnings

    def test_bare_makefile_has_no_metadata(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:\n\tcc main.c\n", encoding="utf-8")
        info = parse_make(tmp_path)
        assert info.name == ""
        assert info.version == ""
        assert info.warnings


# ── map_dependencies dedup / empty ──────────────────────────────


class TestMapDependencies:
    def test_skips_empty_and_duplicate_names(self):
        resolved, unresolved = map_dependencies(["", "zlib", "zlib"], {"zlib"})
        assert resolved == ["zlib"]
        assert unresolved == []

    def test_alias_collision_resolves_once(self):
        # png and libpng16 both alias to libpng; it lands in depends: only once.
        resolved, _ = map_dependencies(["png", "libpng16"], {"libpng"})
        assert resolved == ["libpng"]
