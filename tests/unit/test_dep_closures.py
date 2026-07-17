"""Placement rule: dependency *edge* decides the prefix.

``depends.runtime`` closure → install prefix (it ships).
``depends.build`` closure  → build prefix (build-time only, stripped on install).

Neither ``platform: any`` nor "source-ness" affects placement — only the edge.
"""

from __future__ import annotations

import yaml

from cvcpkg.builder import Recipe, resolve_dep_closures


def _mk(tmp_path, name, *, build=None, runtime=None, host_tools=None, platform="linux"):
    d = tmp_path / "recipes" / name
    d.mkdir(parents=True)
    raw = {
        "schema_version": 1,
        "recipe": {"name": name, "upstream_version": "1.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "."},
        "build": {"matrix": [{"platform": platform, "script": "build.sh"}]},
    }
    depends = {}
    if build:
        depends["build"] = build
    if runtime:
        depends["runtime"] = runtime
    if host_tools:
        depends["host_tools"] = host_tools
    if depends:
        raw["depends"] = depends
    (d / "recipe.yaml").write_text(yaml.safe_dump(raw))
    (d / "build.sh").write_text("#!/bin/sh\ntrue\n")
    return Recipe.load(d)


def _recipes(*rs):
    return {r.name: r for r in rs}


class TestDepClosures:
    def test_runtime_dep_ships_build_dep_does_not(self, tmp_path):
        app = _mk(tmp_path, "app", build=["srcpkg"], runtime=["zlib"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app), "linux")
        assert rt == {"zlib"}  # ships → install prefix
        assert bt == {"srcpkg"}  # build-time only → build prefix

    def test_any_platform_runtime_dep_still_ships(self, tmp_path):
        # 'any' is NOT special: a noarch runtime dep belongs in the install prefix.
        data = _mk(tmp_path, "data", platform="any")
        app = _mk(tmp_path, "app", runtime=["data"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app, data), "linux")
        assert rt == {"data"}
        assert bt == set()

    def test_any_platform_build_dep_goes_to_build_prefix(self, tmp_path):
        # Same 'any' package consumed as a build dep → build prefix.
        src = _mk(tmp_path, "mathsrc", platform="any")
        app = _mk(tmp_path, "app", build=["mathsrc"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app, src), "linux")
        assert rt == set()
        assert bt == {"mathsrc"}

    def test_runtime_closure_is_transitive(self, tmp_path):
        c = _mk(tmp_path, "c")
        b = _mk(tmp_path, "b", runtime=["c"])
        a = _mk(tmp_path, "a", runtime=["b"])
        rt, bt = resolve_dep_closures(["a"], _recipes(a, b, c), "linux")
        assert rt == {"b", "c"}
        assert bt == set()

    def test_build_dep_pulls_its_own_runtime_deps_into_build_prefix(self, tmp_path):
        # A build tool must be able to run → its runtime deps are build-time too.
        libz = _mk(tmp_path, "libz")
        tool = _mk(tmp_path, "tool", runtime=["libz"])
        app = _mk(tmp_path, "app", build=["tool"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app, tool, libz), "linux")
        assert rt == set()
        assert bt == {"tool", "libz"}

    def test_build_dep_of_a_shipped_dep_is_build_time(self, tmp_path):
        # zlib ships; the tool needed to build zlib does not.
        gen = _mk(tmp_path, "gen")
        zlib = _mk(tmp_path, "zlib", build=["gen"])
        app = _mk(tmp_path, "app", runtime=["zlib"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app, zlib, gen), "linux")
        assert rt == {"zlib"}
        assert bt == {"gen"}

    def test_shipping_wins_when_reachable_both_ways(self, tmp_path):
        # zlib is both a build dep and a runtime dep → install prefix only,
        # never duplicated into the build prefix.
        zlib = _mk(tmp_path, "zlib")
        app = _mk(tmp_path, "app", build=["zlib"], runtime=["zlib"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app, zlib), "linux")
        assert rt == {"zlib"}
        assert "zlib" not in bt

    def test_host_tools_are_build_time(self, tmp_path):
        cmake = _mk(tmp_path, "cmake")
        app = _mk(tmp_path, "app", host_tools=["cmake"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app, cmake), "linux")
        assert rt == set()
        assert bt == {"cmake"}

    def test_targets_are_in_neither_closure(self, tmp_path):
        app = _mk(tmp_path, "app", runtime=["zlib"])
        zlib = _mk(tmp_path, "zlib")
        rt, bt = resolve_dep_closures(["app"], _recipes(app, zlib), "linux")
        assert "app" not in rt and "app" not in bt

    def test_platform_scoped_dep_excluded(self, tmp_path):
        app = _mk(tmp_path, "app", runtime=[{"name": "winonly", "platforms": ["windows"]}])
        win = _mk(tmp_path, "winonly", platform="windows")
        rt, bt = resolve_dep_closures(["app"], _recipes(app, win), "linux")
        assert rt == set()
        assert bt == set()

    def test_cycle_terminates(self, tmp_path):
        a = _mk(tmp_path, "a", runtime=["b"])
        b = _mk(tmp_path, "b", runtime=["a"])
        rt, bt = resolve_dep_closures(["a"], _recipes(a, b), "linux")
        assert "b" in rt  # and it returns rather than hanging

    def test_unknown_dep_is_tolerated(self, tmp_path):
        app = _mk(tmp_path, "app", build=["not-in-tree"])
        rt, bt = resolve_dep_closures(["app"], _recipes(app), "linux")
        assert bt == {"not-in-tree"}


class TestAnyBuildDepPlatformSelection:
    """A platform-independent build dep (a source package) must be built ONCE,
    natively — never "for" the target platform.

    Regression: building an `any` source package with platform=windows from a
    WSL host handed its build.sh to winhost delegation, which only runs .ps1:
    "winhost delegation only supports .ps1 build scripts, got build.sh".
    """

    def _pick(self, recipe, target):
        # mirrors the rule the build CLI applies to build-closure recipes
        return "any" if all(m.platform == "any" for m in recipe.build_matrix) else target

    def test_any_only_recipe_builds_as_any_not_target(self, tmp_path):
        src = _mk(tmp_path, "mysrc", platform="any")
        assert self._pick(src, "windows") == "any"
        assert self._pick(src, "linux") == "any"

    def test_platform_specific_build_dep_builds_for_target(self, tmp_path):
        tool = _mk(tmp_path, "tool", platform="windows")
        assert self._pick(tool, "windows") == "windows"
