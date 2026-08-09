"""Tests for scripts/validate_all_recipes.py — the dependency-closure gate.

The closure invariant: a recipe declaring ``platform: P`` must have every
dependency that applies on P also buildable on P.  A violation means the recipe
advertises a platform it can never build, which otherwise only surfaces when a
builder claims the job.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("yaml")

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_all_recipes.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_all_recipes", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _doc(platforms, *, build=None, runtime=None, host_platforms=None):
    """Build a minimal recipe doc.  *platforms* may carry a host_platform."""
    matrix = []
    for p in platforms:
        entry = {"platform": p, "script": "build.sh"}
        if host_platforms and p in host_platforms:
            entry["host_platform"] = host_platforms[p]
        matrix.append(entry)
    return {
        "build": {"matrix": matrix},
        "depends": {"build": build or [], "runtime": runtime or []},
    }


class TestCoverage:
    def test_any_platform_covers_everything(self):
        mod = _load()
        assert mod.covers(_doc(["any"]), "netbsd")
        assert mod.covers(_doc(["any"]), "wasm")

    def test_concrete_platform_covers_only_itself(self):
        mod = _load()
        assert mod.covers(_doc(["linux"]), "linux")
        assert not mod.covers(_doc(["linux"]), "macos")


class TestClosure:
    def test_clean_graph_has_no_errors(self):
        mod = _load()
        docs = {
            "lib": _doc(["linux", "macos"]),
            "app": _doc(["linux", "macos"], runtime=[{"name": "lib"}]),
        }
        assert mod.check_closure(docs) == []

    def test_dependency_missing_the_platform_is_reported(self):
        mod = _load()
        docs = {
            "lib": _doc(["linux"]),
            "app": _doc(["linux", "macos"], runtime=[{"name": "lib"}]),
        }
        errors = mod.check_closure(docs)
        assert len(errors) == 1
        assert "app" in errors[0] and "macos" in errors[0] and "lib" in errors[0]

    def test_platforms_filter_scopes_the_edge(self):
        mod = _load()
        # The same broken graph, but the edge is declared not to apply on macos.
        docs = {
            "lib": _doc(["linux"]),
            "app": _doc(["linux", "macos"], runtime=[{"name": "lib", "platforms": ["linux"]}]),
        }
        assert mod.check_closure(docs) == []

    def test_string_shorthand_dependency_is_understood(self):
        mod = _load()
        docs = {"lib": _doc(["linux"]), "app": _doc(["linux", "macos"], build=["lib"])}
        assert len(mod.check_closure(docs)) == 1

    def test_unknown_dependency_is_ignored_here(self):
        mod = _load()
        # Provided slots / external names are validate_cross_deps' job, not ours.
        docs = {"app": _doc(["linux"], runtime=[{"name": "nowhere"}])}
        assert mod.check_closure(docs) == []

    def test_noarch_dependency_satisfies_every_platform(self):
        mod = _load()
        docs = {
            "purelib": _doc(["any"]),
            "app": _doc(["linux", "netbsd"], runtime=[{"name": "purelib"}]),
        }
        assert mod.check_closure(docs) == []


class TestCrossTargetHostTools:
    def test_build_dep_resolves_on_the_host_for_cross_targets(self):
        mod = _load()
        # cmake builds on linux only; a wasm entry hosted on linux may use it as
        # a host tool even though cmake itself has no wasm build.
        docs = {
            "cmake": _doc(["linux"]),
            "lib": _doc(["wasm"], build=[{"name": "cmake"}], host_platforms={"wasm": "linux"}),
        }
        assert mod.check_closure(docs) == []

    def test_runtime_dep_must_still_cover_the_cross_target(self):
        mod = _load()
        # A runtime dep is linked into the artifact, so the host cannot satisfy it.
        docs = {
            "zstd": _doc(["linux"]),
            "lib": _doc(["wasm"], runtime=[{"name": "zstd"}], host_platforms={"wasm": "linux"}),
        }
        errors = mod.check_closure(docs)
        assert len(errors) == 1
        assert "zstd" in errors[0] and "wasm" in errors[0]

    def test_host_tool_absent_on_the_declared_host_is_reported(self):
        mod = _load()
        docs = {
            "emsdk": _doc(["macos"]),
            "lib": _doc(["wasm"], build=[{"name": "emsdk"}], host_platforms={"wasm": "linux"}),
        }
        assert len(mod.check_closure(docs)) == 1


def test_real_recipe_tree_is_closed():
    """The shipped recipes/ tree must satisfy the invariant."""
    mod = _load()
    recipes_dir = Path(__file__).resolve().parents[2] / "recipes"
    docs = {rf.parent.name: mod.load(rf) for rf in sorted(recipes_dir.glob("*/recipe.yaml"))}
    assert mod.check_closure(docs) == []
