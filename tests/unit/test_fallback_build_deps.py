"""--fallback-to-source builds a component AFTER its own build and runtime deps.

A component with no bundle for the platform but a recipe that builds there is
built into the prefix from that recipe. Its build runs against the prefix, so
its recipe's depends.build and runtime deps have to be resolved (from the
catalog, or source-built in turn) and installed first. Before this, such a
component was built against whatever the rest of the request happened to pull
in: `install-deps numpy-cp312 --fallback-to-source` on Windows built
cython-cp312 without setuptools-cp312/wheel-cp312 (nothing else needed them)
and the build died with "Cannot import 'setuptools.build_meta'".
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _write_recipe(
    root: Path,
    name: str,
    *,
    build=(),
    runtime=None,
    platforms=("linux",),
    scoped=None,
):
    """A minimal recipe. *scoped* maps a build-dep name to its platforms list."""
    rdir = root / name
    rdir.mkdir(parents=True)
    scoped = scoped or {}

    def deps_block(names):
        if not names:
            return " []"
        out = ""
        for d in names:
            out += f"\n    - name: {d}"
            if d in scoped:
                out += f"\n      platforms: [{', '.join(scoped[d])}]"
        return out

    runtime_yaml = "" if runtime is None else f"\n  runtime:{deps_block(runtime)}"
    matrix = "".join(f"    - platform: {p}\n      script: build.sh\n" for p in platforms)
    (rdir / "recipe.yaml").write_text(f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "1.0.0"
  cvc_revision: 1
  license: MIT
source:
  type: none
depends:
  build:{deps_block(build)}{runtime_yaml}
build:
  matrix:
{matrix}package:
  files:
    - bin/
""")
    (rdir / "build.sh").write_text("#!/bin/sh\nexit 0\n")
    return rdir


def _bundle(name, deps=()):
    return {
        "name": name,
        "version": "1.0.0+cvc.1",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "sha256": "abc",
        "size_bytes": 1,
        "archive_url": f"https://cvcpkg.org/a/{name}.tar.gz",
        "required_deps": [{"name": d} for d in deps],
    }


class TestSourceBuildDeps:
    def test_build_deps_then_runtime_deps_for_the_platform(self, tmp_path):
        from cvcpkg.installer import source_build_deps

        root = tmp_path / "recipes"
        _write_recipe(
            root,
            "cythonx",
            build=("pyx", "setuptoolsx", "wheelx", "patchelfx"),
            runtime=("pyx", "libfoo"),
            platforms=("linux", "windows"),
            scoped={"patchelfx": ["linux"]},
        )
        assert source_build_deps("cythonx", [root], "linux") == [
            "pyx",
            "setuptoolsx",
            "wheelx",
            "patchelfx",
            "libfoo",
        ]
        # The platform-scoped build dep drops out on windows.
        assert source_build_deps("cythonx", [root], "windows") == [
            "pyx",
            "setuptoolsx",
            "wheelx",
            "libfoo",
        ]

    def test_none_without_a_recipe_or_a_build_for_the_platform(self, tmp_path):
        from cvcpkg.installer import source_build_deps

        root = tmp_path / "recipes"
        _write_recipe(root, "linuxonly", build=("pyx",), platforms=("linux",))
        assert source_build_deps("missing", [root], "linux") is None
        assert source_build_deps("linuxonly", [root], "windows") is None
        assert source_build_deps("linuxonly", [root], "linux") == ["pyx"]


def _install(tmp_path, monkeypatch, root, bundles, *args):
    from click.testing import CliRunner

    from cvcpkg import installer
    from cvcpkg.cli import cli

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": bundles}))
    events: list[str] = []
    monkeypatch.setattr(
        installer, "install_entry", lambda entry, *a, **kw: events.append(f"install {entry.name}")
    )
    monkeypatch.setattr(
        installer,
        "build_from_source_fallback",
        lambda name, *a, **kw: events.append(f"build {name}"),
    )
    res = CliRunner().invoke(
        cli,
        [
            "install",
            *args,
            "--catalog",
            str(catalog),
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--recipes-dir",
            str(root),
            "--no-default-recipes",
            "--prefix",
            str(tmp_path / "prefix"),
        ],
    )
    return res, events


class TestInstallFallback:
    def test_a_fallback_build_gets_its_build_deps_from_the_catalog_first(
        self, tmp_path, monkeypatch
    ):
        # The numpy-cp312 case: cythonx has no bundle; its build needs setuptoolsx
        # and wheelx, which nothing else in the request depends on.
        root = tmp_path / "recipes"
        _write_recipe(root, "cythonx", build=("pyx", "setuptoolsx", "wheelx"), runtime=("pyx",))
        bundles = [_bundle("pyx"), _bundle("setuptoolsx", ["pyx"]), _bundle("wheelx", ["pyx"])]
        res, events = _install(
            tmp_path, monkeypatch, root, bundles, "cythonx", "pyx", "--fallback-to-source"
        )
        assert res.exit_code == 0, res.output
        assert events == [
            "install pyx",
            "install setuptoolsx",
            "install wheelx",
            "build cythonx",
        ]
        assert "cythonx has no bundle for this platform" in res.output

    def test_a_fallback_dep_with_no_bundle_is_source_built_first(self, tmp_path, monkeypatch):
        # cythonx's build dep toolx has no bundle either: it is built first.
        root = tmp_path / "recipes"
        _write_recipe(root, "cythonx", build=("pyx", "toolx"), runtime=("pyx",))
        _write_recipe(root, "toolx", build=("pyx",), runtime=("pyx",))
        res, events = _install(
            tmp_path, monkeypatch, root, [_bundle("pyx")], "cythonx", "--fallback-to-source"
        )
        assert res.exit_code == 0, res.output
        assert events == ["install pyx", "build toolx", "build cythonx"]

    def test_a_catalog_bundle_may_depend_on_a_fallback_build(self, tmp_path, monkeypatch):
        # numpyx (published) needs cythonx (no bundle): cythonx's build deps come
        # in too, and it is built before anything that needs it is used.
        root = tmp_path / "recipes"
        _write_recipe(root, "cythonx", build=("pyx", "setuptoolsx"), runtime=("pyx",))
        bundles = [_bundle("numpyx", ["cythonx", "pyx"]), _bundle("pyx"), _bundle("setuptoolsx")]
        res, events = _install(
            tmp_path, monkeypatch, root, bundles, "numpyx", "--fallback-to-source"
        )
        assert res.exit_code == 0, res.output
        assert "install setuptoolsx" in events
        assert events[-1] == "build cythonx"

    def test_without_the_flag_a_missing_bundle_is_still_an_error(self, tmp_path, monkeypatch):
        root = tmp_path / "recipes"
        _write_recipe(root, "cythonx", build=("pyx",))
        res, events = _install(tmp_path, monkeypatch, root, [_bundle("pyx")], "cythonx", "pyx")
        assert res.exit_code != 0
        assert "--fallback-to-source" in res.output
        assert not any(e.startswith("build ") for e in events)

    def test_a_recipe_with_no_build_for_the_platform_is_not_attempted(self, tmp_path, monkeypatch):
        root = tmp_path / "recipes"
        _write_recipe(root, "winonly", build=("pyx",), platforms=("windows",))
        res, events = _install(
            tmp_path, monkeypatch, root, [_bundle("pyx")], "winonly", "--fallback-to-source"
        )
        # Still requested and unbuildable here: it falls through to the old
        # source_only path, whose build then reports the missing matrix entry.
        assert "winonly has no bundle for this platform" not in res.output
