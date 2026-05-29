"""Integration tests for cvcpkg rev-bump.

Exercises revision bumping with dummy recipes that have real
build scripts, verifying that:
- YAML files are correctly updated on disk
- Downstream cascade propagates through real dependency chains
- chain_hash changes when revisions are bumped
- build-all sees the new revisions after a bump
- CLI round-trips produce correct output
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import (
    Recipe,
    RecipeError,
    _bump_revision_in_yaml,
    chain_hash,
    get_downstream,
    get_reverse_deps,
    list_recipes,
    resolve_build_order,
    rev_bump,
)
from cvcpkg.cli import main

# Real recipes dir (skip if not running from repo root)
try:
    REPO_ROOT = Path(__file__).resolve().parents[4]
except IndexError:
    REPO_ROOT = Path("/nonexistent")
RECIPES_DIR = REPO_ROOT / "recipes"

requires_repo = pytest.mark.skipif(
    not RECIPES_DIR.is_dir(),
    reason="Not running from libcvc-deps repo",
)


# ── Helpers ─────────────────────────────────────────────────────


def _make_recipe(
    recipes_dir: Path,
    name: str,
    *,
    deps: list[str] | None = None,
    revision: int = 1,
    version: str = "1.0.0",
) -> Path:
    """Create a dummy recipe with a working build script."""
    rd = recipes_dir / name
    rd.mkdir(parents=True, exist_ok=True)
    recipe = {
        "schema_version": 1,
        "recipe": {
            "name": name,
            "upstream_version": version,
            "cvc_revision": revision,
        },
        "source": {"type": "vendored", "path": "."},
        "patches": [],
        "build": {
            "matrix": [{"platform": "linux", "script": "build.sh"}],
        },
        "package": {"files": ["lib/*"], "cmake_packages": []},
    }
    if deps:
        recipe["depends"] = {"build": [{"name": d} for d in deps]}
    (rd / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
    # Dummy build script that creates a marker file
    (rd / "build.sh").write_text(
        '#!/bin/sh\nmkdir -p "$CVC_INSTALL_DIR/lib"\n'
        f'echo "{name}" > "$CVC_INSTALL_DIR/lib/{name}.txt"\n'
    )
    os.chmod(rd / "build.sh", 0o755)
    return rd


# ── Integration: rev-bump with real YAML round-trip ─────────────


class TestRevBumpYamlRoundTrip:
    """Verify YAML files survive loading, bumping, and reloading."""

    def test_bump_and_reload(self, tmp_path):
        """Bump a recipe's revision and verify Recipe.load sees new value."""
        rd = _make_recipe(tmp_path / "recipes", "mypkg")
        r = Recipe.load(rd)
        assert r.cvc_revision == 1
        assert r.full_version == "1.0.0+cvc.1"

        _bump_revision_in_yaml(rd / "recipe.yaml", 2)
        r2 = Recipe.load(rd)
        assert r2.cvc_revision == 2
        assert r2.full_version == "1.0.0+cvc.2"

    def test_bump_preserves_all_fields(self, tmp_path):
        """Ensure bumping doesn't drop or corrupt other recipe fields."""
        rd = _make_recipe(tmp_path / "recipes", "rich", deps=["dep1", "dep2"])
        _original = yaml.safe_load((rd / "recipe.yaml").read_text())

        _bump_revision_in_yaml(rd / "recipe.yaml", 5)
        updated = yaml.safe_load((rd / "recipe.yaml").read_text())

        # Core fields preserved
        assert updated["recipe"]["name"] == "rich"
        assert updated["recipe"]["upstream_version"] == "1.0.0"
        assert updated["recipe"]["cvc_revision"] == 5
        assert updated["source"]["type"] == "vendored"
        assert len(updated["depends"]["build"]) == 2
        assert updated["build"]["matrix"][0]["platform"] == "linux"

    def test_bump_preserves_comments(self, tmp_path):
        """Lines that aren't cvc_revision are untouched."""
        p = tmp_path / "recipe.yaml"
        content = (
            "schema_version: 1\n"
            "recipe:\n"
            "  name: commented\n"
            "  upstream_version: '2.0.0'\n"
            "  cvc_revision: 1  # build revision\n"
            "  # This is important\n"
            "  description: 'A library'\n"
        )
        p.write_text(content)
        _bump_revision_in_yaml(p, 2)
        text = p.read_text()
        assert "# This is important" in text
        assert "description: 'A library'" in text
        assert "cvc_revision: 2" in text

    def test_repeated_bumps(self, tmp_path):
        """Bump the same recipe 10 times in sequence."""
        rd = _make_recipe(tmp_path / "recipes", "multi")
        for i in range(2, 12):
            _bump_revision_in_yaml(rd / "recipe.yaml", i)
            r = Recipe.load(rd)
            assert r.cvc_revision == i
            assert r.full_version == f"1.0.0+cvc.{i}"


# ── Integration: dependency graph operations ────────────────────


class TestDependencyGraphIntegration:
    """Test reverse deps and downstream with realistic recipe graphs."""

    def _setup_graph(self, tmp_path):
        """Create a realistic 6-recipe dependency graph.

        Graph:
            openssl (root)
            curl    → openssl
            cmake   → curl
            zlib    (independent root)
            grpc    → openssl, zlib
            app     → grpc, cmake
        """
        rd = tmp_path / "recipes"
        _make_recipe(rd, "openssl")
        _make_recipe(rd, "curl", deps=["openssl"])
        _make_recipe(rd, "cmake", deps=["curl"])
        _make_recipe(rd, "zlib")
        _make_recipe(rd, "grpc", deps=["openssl", "zlib"])
        _make_recipe(rd, "app", deps=["grpc", "cmake"])
        return list_recipes(rd), rd

    def test_reverse_deps_structure(self, tmp_path):
        recipes, _ = self._setup_graph(tmp_path)
        rd = get_reverse_deps(recipes)
        assert "curl" in rd["openssl"]
        assert "grpc" in rd["openssl"]
        assert "cmake" in rd["curl"]
        assert "app" in rd["grpc"]
        assert "app" in rd["cmake"]
        assert "grpc" in rd["zlib"]

    def test_downstream_openssl(self, tmp_path):
        """Bumping openssl should cascade to curl, cmake, grpc, app."""
        recipes, _ = self._setup_graph(tmp_path)
        ds = get_downstream("openssl", recipes)
        assert set(ds) == {"curl", "cmake", "grpc", "app"}

    def test_downstream_zlib(self, tmp_path):
        """Bumping zlib cascades to grpc and app."""
        recipes, _ = self._setup_graph(tmp_path)
        ds = get_downstream("zlib", recipes)
        assert set(ds) == {"grpc", "app"}

    def test_downstream_curl(self, tmp_path):
        """Bumping curl cascades to cmake and app."""
        recipes, _ = self._setup_graph(tmp_path)
        ds = get_downstream("curl", recipes)
        assert set(ds) == {"cmake", "app"}

    def test_downstream_leaf(self, tmp_path):
        """app has no downstream dependents."""
        recipes, _ = self._setup_graph(tmp_path)
        ds = get_downstream("app", recipes)
        assert ds == []

    def test_downstream_grpc(self, tmp_path):
        """grpc only has app downstream."""
        recipes, _ = self._setup_graph(tmp_path)
        ds = get_downstream("grpc", recipes)
        assert ds == ["app"]

    def test_independent_root_not_in_openssl_downstream(self, tmp_path):
        """zlib is independent — not in openssl's downstream."""
        recipes, _ = self._setup_graph(tmp_path)
        ds = get_downstream("openssl", recipes)
        assert "zlib" not in ds


# ── Integration: rev_bump with cascade ──────────────────────────


class TestRevBumpCascadeIntegration:
    """Full rev_bump integration exercising YAML edits + reload."""

    def _setup_chain(self, tmp_path):
        """a(rev=1) → b(rev=1) → c(rev=1)"""
        rd = tmp_path / "recipes"
        _make_recipe(rd, "a")
        _make_recipe(rd, "b", deps=["a"])
        _make_recipe(rd, "c", deps=["b"])
        return rd

    def test_cascade_bumps_all(self, tmp_path):
        rd = self._setup_chain(tmp_path)
        bumped = rev_bump("a", rd)
        assert len(bumped) == 3
        for name in ["a", "b", "c"]:
            r = Recipe.load(rd / name)
            assert r.cvc_revision == 2

    def test_no_cascade_bumps_one(self, tmp_path):
        rd = self._setup_chain(tmp_path)
        bumped = rev_bump("a", rd, cascade=False)
        assert len(bumped) == 1
        assert Recipe.load(rd / "a").cvc_revision == 2
        assert Recipe.load(rd / "b").cvc_revision == 1
        assert Recipe.load(rd / "c").cvc_revision == 1

    def test_middle_bump_skips_upstream(self, tmp_path):
        rd = self._setup_chain(tmp_path)
        bumped = rev_bump("b", rd)
        names = [b[0] for b in bumped]
        assert "a" not in names
        assert "b" in names
        assert "c" in names
        assert Recipe.load(rd / "a").cvc_revision == 1
        assert Recipe.load(rd / "b").cvc_revision == 2
        assert Recipe.load(rd / "c").cvc_revision == 2

    def test_diamond_cascade(self, tmp_path):
        """Diamond: a → {b, c} → d. Bump a cascades to all."""
        rd = tmp_path / "recipes"
        _make_recipe(rd, "a")
        _make_recipe(rd, "b", deps=["a"])
        _make_recipe(rd, "c", deps=["a"])
        _make_recipe(rd, "d", deps=["b", "c"])

        bumped = rev_bump("a", rd)
        assert set(b[0] for b in bumped) == {"a", "b", "c", "d"}
        for name in ["a", "b", "c", "d"]:
            assert Recipe.load(rd / name).cvc_revision == 2

    def test_mixed_starting_revisions(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "base", revision=3)
        _make_recipe(rd, "mid", deps=["base"], revision=7)
        _make_recipe(rd, "top", deps=["mid"], revision=1)

        bumped = rev_bump("base", rd)
        revs = {name: new for name, _, new in bumped}
        assert revs["base"] == 4
        assert revs["mid"] == 8
        assert revs["top"] == 2

    def test_bump_nonexistent_recipe(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "a")
        with pytest.raises(RecipeError, match="not found"):
            rev_bump("ghost", rd)

    def test_double_bump(self, tmp_path):
        """Two sequential bumps produce correct revisions."""
        rd = self._setup_chain(tmp_path)
        rev_bump("a", rd)
        bumped2 = rev_bump("a", rd)

        revs = {name: (old, new) for name, old, new in bumped2}
        assert revs["a"] == (2, 3)
        assert revs["b"] == (2, 3)
        assert revs["c"] == (2, 3)


# ── Integration: chain_hash changes after rev-bump ──────────────


class TestChainHashAfterBump:
    """Verify that chain_hash changes when revisions are bumped."""

    def test_chain_hash_changes_for_bumped_recipe(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "a")
        _make_recipe(rd, "b", deps=["a"])

        recipes = list_recipes(rd)
        by_name = {r.name: r for r in recipes}
        hash_before_a = chain_hash(by_name["a"], by_name)
        hash_before_b = chain_hash(by_name["b"], by_name)

        rev_bump("a", rd, cascade=False)

        recipes2 = list_recipes(rd)
        by_name2 = {r.name: r for r in recipes2}
        hash_after_a = chain_hash(by_name2["a"], by_name2)
        hash_after_b = chain_hash(by_name2["b"], by_name2)

        # a's hash must change (its own recipe.yaml changed)
        assert hash_before_a != hash_after_a
        # b's hash must also change (a is a transitive dep)
        assert hash_before_b != hash_after_b

    def test_chain_hash_unchanged_for_unrelated(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "x")
        _make_recipe(rd, "y")  # independent of x

        recipes = list_recipes(rd)
        by_name = {r.name: r for r in recipes}
        hash_before = chain_hash(by_name["y"], by_name)

        rev_bump("x", rd, cascade=False)

        recipes2 = list_recipes(rd)
        by_name2 = {r.name: r for r in recipes2}
        hash_after = chain_hash(by_name2["y"], by_name2)

        assert hash_before == hash_after

    def test_cascaded_bump_changes_all_hashes(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "a")
        _make_recipe(rd, "b", deps=["a"])
        _make_recipe(rd, "c", deps=["b"])

        recipes = list_recipes(rd)
        by_name = {r.name: r for r in recipes}
        hashes_before = {n: chain_hash(r, by_name) for n, r in by_name.items()}

        rev_bump("a", rd)  # cascades to b and c

        recipes2 = list_recipes(rd)
        by_name2 = {r.name: r for r in recipes2}
        hashes_after = {n: chain_hash(r, by_name2) for n, r in by_name2.items()}

        for name in ["a", "b", "c"]:
            assert hashes_before[name] != hashes_after[name], (
                f"{name} chain_hash should change after cascade bump"
            )


# ── Integration: build_order after rev-bump ─────────────────────


class TestBuildOrderAfterBump:
    """Ensure resolve_build_order still works after rev-bump."""

    def test_order_unchanged_after_bump(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "a")
        _make_recipe(rd, "b", deps=["a"])
        _make_recipe(rd, "c", deps=["a"])
        _make_recipe(rd, "d", deps=["b", "c"])

        recipes = list_recipes(rd)
        order_before = [r.name for r in resolve_build_order(recipes)]

        rev_bump("a", rd)

        recipes2 = list_recipes(rd)
        order_after = [r.name for r in resolve_build_order(recipes2)]

        # Build order topology should be the same
        assert order_before == order_after

    def test_bumped_revisions_visible_in_build_order(self, tmp_path):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "base")
        _make_recipe(rd, "app", deps=["base"])

        rev_bump("base", rd)

        recipes = list_recipes(rd)
        ordered = resolve_build_order(recipes)
        versions = {r.name: r.full_version for r in ordered}
        assert versions["base"] == "1.0.0+cvc.2"
        assert versions["app"] == "1.0.0+cvc.2"


# ── Integration: CLI rev-bump end-to-end ────────────────────────


class TestRevBumpCliIntegration:
    """Test the rev-bump CLI with real file operations."""

    def test_cli_cascade(self, tmp_path, capsys):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "lib")
        _make_recipe(rd, "tool", deps=["lib"])
        _make_recipe(rd, "app", deps=["tool"])

        ret = main(["rev-bump", "lib", "--recipes-dir", str(rd)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "lib: cvc_revision 1 → 2" in out
        assert "tool: cvc_revision 1 → 2" in out
        assert "app: cvc_revision 1 → 2" in out
        assert "3 recipe(s) bumped" in out

        # Verify files
        for name in ["lib", "tool", "app"]:
            r = Recipe.load(rd / name)
            assert r.cvc_revision == 2

    def test_cli_no_cascade(self, tmp_path, capsys):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "lib")
        _make_recipe(rd, "app", deps=["lib"])

        ret = main(
            [
                "rev-bump",
                "lib",
                "--no-cascade",
                "--recipes-dir",
                str(rd),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "1 recipe(s) bumped" in out
        assert Recipe.load(rd / "lib").cvc_revision == 2
        assert Recipe.load(rd / "app").cvc_revision == 1

    def test_cli_nonexistent_recipe(self, tmp_path, capsys):
        rd = tmp_path / "recipes"
        _make_recipe(rd, "exists")

        ret = main(["rev-bump", "nope", "--recipes-dir", str(rd)])
        assert ret == 1

    def test_cli_sequential_bumps(self, tmp_path, capsys):
        """Two CLI invocations produce monotonically increasing revisions."""
        rd = tmp_path / "recipes"
        _make_recipe(rd, "pkg")

        main(["rev-bump", "pkg", "--recipes-dir", str(rd)])
        capsys.readouterr()  # clear

        main(["rev-bump", "pkg", "--recipes-dir", str(rd)])
        out = capsys.readouterr().out
        assert "pkg: cvc_revision 2 → 3" in out
        assert Recipe.load(rd / "pkg").cvc_revision == 3


# ── Integration: real recipes (skipped if not in repo) ──────────


@requires_repo
class TestRevBumpRealRecipes:
    """Smoke tests against the actual recipes/ directory.

    These are read-only — they DON'T modify real recipe files.
    They verify that the dependency graph analysis works on the
    real recipe set.
    """

    def test_openssl_has_downstream(self):
        recipes = list_recipes(RECIPES_DIR)
        ds = get_downstream("openssl", recipes)
        assert len(ds) > 5  # openssl has many dependents
        assert "curl" in ds
        assert "cmake" in ds

    def test_leaf_recipe_has_no_downstream(self):
        """Find a recipe with no dependents and verify."""
        recipes = list_recipes(RECIPES_DIR)
        rd = get_reverse_deps(recipes)
        # qt6-wasm-singlethread is typically a leaf
        for r in recipes:
            if r.name not in rd:
                ds = get_downstream(r.name, recipes)
                assert ds == [], f"{r.name} should have no downstream"
                break

    def test_reverse_deps_consistent_with_downstream(self):
        """reverse_deps and downstream should be consistent."""
        recipes = list_recipes(RECIPES_DIR)
        rd = get_reverse_deps(recipes)

        for name, dependants in rd.items():
            for dep in dependants:
                ds = get_downstream(name, recipes)
                assert dep in ds, (
                    f"{dep} is a direct dependant of {name} but not in get_downstream({name})"
                )

    def test_all_recipes_loadable_after_hypothetical_bump(self):
        """Verify the recipe set is valid for rev_bump analysis."""
        recipes = list_recipes(RECIPES_DIR)
        _by_name = {r.name: r for r in recipes}
        # Every recipe's deps should be in the set
        for r in recipes:
            for dep in r.raw.get("depends", {}).get("build", []):
                dep_name = dep if isinstance(dep, str) else dep["name"]
                # Some deps are optional/external — just check it doesn't crash
                get_downstream(dep_name, recipes)
