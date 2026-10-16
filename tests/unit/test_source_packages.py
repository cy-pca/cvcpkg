"""Source packages: ``source: {type: package}`` and tools/make_source_package.py.

A source recipe (docs/source-recipes.md) stages an upstream tree at
``src/<name>/``; a recipe whose source is ``{type: package, package: <name>}``
builds from a copy of that tree instead of downloading upstream.  The 2026-10-15
linux/arm64 populate could not download 40-odd freedesktop.org/X.org tarballs
(connection timeouts from the runners); built from source packages, only the
builder that publishes the noarch source package ever contacts upstream.
"""

from __future__ import annotations

import importlib.util
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import (
    SOURCE_PACKAGES_CAPABILITY,
    Recipe,
    RecipeError,
    _prefix_files,
    _rewrite_pc_prefixes,
    fetch_source,
)
from cvcpkg.validation import validate_recipe_dir

ROOT = Path(__file__).resolve().parents[2]


def _recipe(tmp_path: Path, source: dict, build_deps: list | None = None) -> Recipe:
    rd = tmp_path / "recipes" / "libfoo"
    rd.mkdir(parents=True)
    doc = {
        "schema_version": 1,
        "recipe": {"name": "libfoo", "upstream_version": "1.2.0", "cvc_revision": 1},
        "source": source,
        "depends": {"build": build_deps or []},
        "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
        "package": {"files": ["lib/"]},
    }
    (rd / "recipe.yaml").write_text(yaml.safe_dump(doc))
    (rd / "build.sh").write_text("#!/bin/sh\n")
    return Recipe.load(rd)


class TestFetch:
    def test_copies_the_staged_tree_from_the_first_prefix_that_has_it(self, tmp_path):
        r = _recipe(tmp_path, {"type": "package", "package": "libfoo-src"}, ["libfoo-src"])
        build_prefix, deps = tmp_path / "p.build", tmp_path / "p"
        staged = build_prefix / "src" / "libfoo-src"
        (staged / "sub").mkdir(parents=True)
        (staged / "configure").write_text("#!/bin/sh\n")
        (staged / "sub" / "foo.c").write_text("int foo;\n")
        work = tmp_path / "work"
        work.mkdir()

        got = fetch_source(r, work, source_prefixes=(None, build_prefix, deps))

        assert got == work / "src"
        assert (got / "sub" / "foo.c").read_text() == "int foo;\n"
        # A copy: writing to it leaves the staged tree alone.
        (got / "foo.o").write_text("x")
        assert not (staged / "foo.o").exists()

    def test_the_copy_has_one_mtime_so_autotools_output_is_not_stale(self, tmp_path):
        # Staged by a plain `cp -R`: configure.ac came out newer than the
        # aclocal.m4 generated from it, and make tried to rerun aclocal-1.16.
        r = _recipe(tmp_path, {"type": "package", "package": "libfoo-src"}, ["libfoo-src"])
        staged = tmp_path / "p" / "src" / "libfoo-src"
        (staged / "m4").mkdir(parents=True)
        for rel, t in (("aclocal.m4", 1_000), ("configure", 1_000), ("configure.ac", 2_000)):
            (staged / rel).write_text("x\n")
            os.utime(staged / rel, (t, t))
        (staged / "m4" / "libtool.m4").write_text("x\n")
        work = tmp_path / "work"
        work.mkdir()

        got = fetch_source(r, work, source_prefixes=(tmp_path / "p",))

        mtimes = {p.stat().st_mtime for p in got.rglob("*")}
        assert len(mtimes) == 1, mtimes
        assert (got / "aclocal.m4").stat().st_mtime >= (got / "configure.ac").stat().st_mtime
        # The staged tree itself is left as it was.
        assert (staged / "configure.ac").stat().st_mtime == 2_000

    def test_not_staged_is_an_error_naming_where_it_looked(self, tmp_path):
        r = _recipe(tmp_path, {"type": "package", "package": "libfoo-src"}, ["libfoo-src"])
        work = tmp_path / "work"
        work.mkdir()
        with pytest.raises(RecipeError, match="libfoo-src is not staged.*depends.build"):
            fetch_source(r, work, source_prefixes=(tmp_path / "p",))


class TestValidation:
    def test_package_must_be_a_build_dependency(self, tmp_path):
        r = _recipe(tmp_path, {"type": "package", "package": "libfoo-src"}, ["zlib"])
        errs = validate_recipe_dir(r.recipe_dir)
        assert any("'libfoo-src' is not in depends.build" in e for e in errs), errs

    def test_named_build_dependency_with_a_pin_is_accepted(self, tmp_path):
        dep = [{"name": "libfoo-src", "version": "==1.2.0"}]
        r = _recipe(tmp_path, {"type": "package", "package": "libfoo-src"}, dep)
        assert validate_recipe_dir(r.recipe_dir) == []

    def test_package_name_is_required(self, tmp_path):
        r = _recipe(tmp_path, {"type": "package"}, [])
        errs = validate_recipe_dir(r.recipe_dir)
        assert any("needs source.package" in e for e in errs), errs


class TestPrefixWalks:
    def test_src_is_left_out_of_prefix_rewrites(self, tmp_path):
        prefix = tmp_path / "prefix"
        (prefix / "lib" / "pkgconfig").mkdir(parents=True)
        (prefix / "src" / "foo-src").mkdir(parents=True)
        installed = prefix / "lib" / "pkgconfig" / "foo.pc"
        upstream = prefix / "src" / "foo-src" / "foo.pc"
        installed.write_text("prefix=/tmp/cvcpkg-foo-abcd1234/install\n")
        upstream.write_text("prefix=/usr\n")

        _rewrite_pc_prefixes(prefix)

        assert installed.read_text() == f"prefix={prefix}\n"
        assert upstream.read_text() == "prefix=/usr\n"
        assert all("src" not in p.relative_to(prefix).parts[:1] for p in _prefix_files(prefix))


def test_builders_advertise_and_package_jobs_require_the_capability():
    builder_src = (ROOT / "src" / "cvcpkg" / "cli" / "_builder.py").read_text()
    builds_src = (ROOT / "src" / "cvcpkg" / "cli" / "_builds.py").read_text()
    assert SOURCE_PACKAGES_CAPABILITY == "source_packages"
    assert "advertised_caps.add(SOURCE_PACKAGES_CAPABILITY)" in builder_src
    assert "caps.append(SOURCE_PACKAGES_CAPABILITY)" in builds_src


class TestGenerator:
    def _load(self):
        spec = importlib.util.spec_from_file_location(
            "make_source_package", ROOT / "tools" / "make_source_package.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_splits_a_tarball_recipe(self, tmp_path):
        recipes = tmp_path / "recipes"
        rd = recipes / "libxau"
        rd.mkdir(parents=True)
        (rd / "build.sh").write_text("#!/bin/sh\n")
        (rd / "recipe.yaml").write_text(
            textwrap.dedent(
                """\
                schema_version: 1
                recipe:
                  name: libxau
                  upstream_version: "1.0.12"
                  cvc_revision: 1
                  license: MIT
                  homepage: https://www.x.org/

                source:
                  type: tarball
                  url: https://xorg.freedesktop.org/releases/individual/lib/libXau-1.0.12.tar.xz
                  sha256: "74d0e4dfa3d39ad8939e99bda37f5967aba528211076828464d2777d477fc0fb"
                  strip_components: 1

                # libxau has no patches.
                patches: []

                depends:
                  build:
                    - name: xorgproto
                  runtime: []

                build:
                  matrix:
                    - platform: linux
                      script: build.sh

                package:
                  files:
                    - lib/libXau*
                """
            )
        )
        assert self._load().main(["--recipes-dir", str(recipes), "libxau"]) == 0

        new = yaml.safe_load((rd / "recipe.yaml").read_text())
        assert new["source"] == {"type": "package", "package": "libxau-src"}
        assert {"name": "libxau-src", "version": "==1.0.12"} in new["depends"]["build"]
        assert {"name": "xorgproto"} in new["depends"]["build"]
        assert "# libxau has no patches." in (rd / "recipe.yaml").read_text()

        src = yaml.safe_load((recipes / "libxau-src" / "recipe.yaml").read_text())
        assert src["source"]["sha256"].startswith("74d0e4df")
        assert src["build"]["matrix"] == [{"platform": "any", "script": "build.sh"}]
        assert src["package"]["files"] == ["src/libxau-src/"]
        assert "cvc_stage_source" in (recipes / "libxau-src" / "build.sh").read_text()

        # Both validate (the consumer's dependency check included).
        (recipes / "_common").mkdir()
        assert validate_recipe_dir(rd) == []
        assert validate_recipe_dir(recipes / "libxau-src") == []

    def test_refuses_what_it_cannot_split(self, tmp_path):
        recipes = tmp_path / "recipes"
        rd = recipes / "wheel"
        rd.mkdir(parents=True)
        (rd / "recipe.yaml").write_text(
            "schema_version: 1\nrecipe: {name: wheel, upstream_version: '1.0', cvc_revision: 1}\n"
            "source: {type: python_wheel, url: x}\n"
        )
        assert self._load().main(["--recipes-dir", str(recipes), "wheel"]) == 1
        assert not (recipes / "wheel-src").exists()
