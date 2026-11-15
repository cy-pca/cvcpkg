"""A recipe with build deps declares its runtime deps.

``runtime_dependencies`` reads ``depends.runtime`` and, when a recipe has no
such key, falls back to ``depends.build``.  For a recipe whose build deps are
tools and source packages, that fallback publishes them as the bundle's runtime
deps.  On 2026-11-14 cvcpkg.org served f2c, gperf and perl rows that required
their ``-src`` packages, xkbcommon and wayland-protocols rows that required
meson, ninja and pkg-config, and cvcpkg rows that required python312 and
pyinstaller.  An install of any of them pulled in the build's toolchain.

The fallback stays for recipes outside this repo.  These tests pin down the
guards around it:

* ``cvcpkg validate`` refuses a non-empty ``depends.build`` with no
  ``depends.runtime`` key, and accepts ``runtime: []``;
* packing such a recipe warns, naming the deps it records;
* the seven recipes that took the fallback now declare what they need at run
  time: nothing for six of them, and imagemagick's codec libraries, unchanged
  from what its rows already record.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import (
    Recipe,
    falls_back_to_build_deps,
    generate_manifest,
    runtime_dependencies,
)
from cvcpkg.validation import validate_recipe_dir

ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / "recipes"


def _recipe(tmp_path: Path, depends: dict | None) -> Recipe:
    rd = tmp_path / "recipes" / "libfoo"
    rd.mkdir(parents=True)
    doc = {
        "schema_version": 1,
        "recipe": {"name": "libfoo", "upstream_version": "1.2.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "."},
        "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
        "package": {"files": ["lib/"]},
    }
    if depends is not None:
        doc["depends"] = depends
    (rd / "recipe.yaml").write_text(yaml.safe_dump(doc))
    (rd / "build.sh").write_text("#!/bin/sh\n")
    return Recipe.load(rd)


class TestFallsBackToBuildDeps:
    @pytest.mark.parametrize(
        "depends, expected",
        [
            ({"build": ["cmake"]}, True),
            ({"build": [{"name": "libfoo-src", "version": "==1.2.0"}], "host_tools": []}, True),
            ({"build": ["cmake"], "runtime": []}, False),
            ({"build": ["cmake"], "runtime": ["zlib"]}, False),
            ({"build": [], "host_tools": ["cmake"]}, False),
            ({"runtime": ["zlib"]}, False),
            ({}, False),
            (None, False),
        ],
    )
    def test_only_a_non_empty_build_list_without_a_runtime_key(self, depends, expected):
        raw = {} if depends is None else {"depends": depends}
        assert falls_back_to_build_deps(raw) is expected

    def test_it_is_exactly_when_runtime_dependencies_reads_build(self, tmp_path):
        r = _recipe(tmp_path, {"build": ["cmake", "libfoo-src"]})
        assert falls_back_to_build_deps(r.raw)
        assert [d["name"] for d in runtime_dependencies(r, "linux")] == ["cmake", "libfoo-src"]


class TestValidation:
    def test_build_deps_without_a_runtime_key_are_refused(self, tmp_path):
        r = _recipe(tmp_path, {"build": ["cmake"]})
        errs = validate_recipe_dir(r.recipe_dir)
        assert any("depends.runtime is missing" in e for e in errs), errs

    @pytest.mark.parametrize(
        "depends",
        [
            {"build": ["cmake"], "runtime": []},
            {"build": ["cmake"], "runtime": [{"name": "zlib", "version": "^1.3"}]},
            {"build": [], "host_tools": ["cmake"]},
            {"runtime": ["zlib"]},
            None,
        ],
    )
    def test_a_declared_runtime_or_no_build_deps_pass(self, tmp_path, depends):
        r = _recipe(tmp_path, depends)
        assert validate_recipe_dir(r.recipe_dir) == []


class TestPackWarning:
    def _manifest(self, tmp_path: Path, r: Recipe) -> dict:
        install = tmp_path / "install"
        (install / "lib").mkdir(parents=True)
        (install / "lib" / "libfoo.so").write_text("lib")
        return generate_manifest(r, install, "linux", "x86_64", "release", "shared")

    def test_packing_a_recipe_that_takes_the_fallback_warns(self, tmp_path, capsys):
        r = _recipe(tmp_path, {"build": ["cmake", "libfoo-src"]})
        manifest = self._manifest(tmp_path, r)
        err = capsys.readouterr().err
        assert "recipe 'libfoo' has depends.build but no depends.runtime" in err
        assert "(cmake, libfoo-src)" in err
        # The warning changes nothing about what the bundle records.
        assert [d["name"] for d in manifest["dependencies"]["required"]] == [
            "cmake",
            "libfoo-src",
        ]

    def test_a_declared_runtime_packs_quietly(self, tmp_path, capsys):
        r = _recipe(tmp_path, {"build": ["cmake"], "runtime": []})
        manifest = self._manifest(tmp_path, r)
        assert "depends.runtime" not in capsys.readouterr().err
        assert manifest["dependencies"]["required"] == []


def _matrix_platforms(r: Recipe) -> list[str]:
    return sorted({m.platform for m in r.build_matrix})


@pytest.mark.parametrize(
    "name", ["cvcpkg", "f2c", "gperf", "perl", "wayland-protocols", "xkbcommon"]
)
def test_tools_and_data_packages_need_nothing_at_run_time(name):
    r = Recipe.load(RECIPES / name)
    assert not falls_back_to_build_deps(r.raw)
    for plat in _matrix_platforms(r):
        assert runtime_dependencies(r, plat) == [], plat


IMAGEMAGICK_LIBS = [
    "zlib",
    "bzip2",
    "tiff",
    "libjpeg-turbo",
    "libpng",
    "libwebp",
    "xz",
    "zstd",
    "lerc",
]


@pytest.mark.parametrize(
    "plat, expected",
    [
        ("linux", [*IMAGEMAGICK_LIBS, "freetype", "fontconfig", "gcc-runtime", "libxml2"]),
        ("macos", [*IMAGEMAGICK_LIBS, "freetype", "fontconfig", "libxml2"]),
        ("windows", [*IMAGEMAGICK_LIBS, "libxml2"]),
        ("wasm", [*IMAGEMAGICK_LIBS, "libxml2"]),
    ],
)
def test_imagemagick_records_the_libraries_it_links(plat, expected):
    # The same names its rows recorded through the fallback, now from
    # depends.runtime, so no published row changes.
    r = Recipe.load(RECIPES / "imagemagick")
    assert not falls_back_to_build_deps(r.raw)
    assert [d["name"] for d in runtime_dependencies(r, plat)] == expected
