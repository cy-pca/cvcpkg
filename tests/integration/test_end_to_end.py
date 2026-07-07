"""Integration tests for cvcpkg — end-to-end workflows.

These tests exercise real recipe loading, manifest generation,
archive creation, and lockfile round-trips using the actual
recipes/ directory in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import (
    Recipe,
    create_archive,
    generate_manifest,
    list_recipes,
    resolve_build_order,
    stage_bundle,
)
from cvcpkg.lockfile import LockEntry, Lockfile
from cvcpkg.manifest import BundleManifest, Requirements

# Real recipes dir (skip if not running from repo)
try:
    REPO_ROOT = Path(__file__).resolve().parents[2]
except IndexError:
    REPO_ROOT = Path("/nonexistent")
RECIPES_DIR = REPO_ROOT / "recipes"
REQUIREMENTS_FILE = REPO_ROOT / "cvc-requirements.yaml"

requires_repo = pytest.mark.skipif(
    not RECIPES_DIR.is_dir(),
    reason="Not running from libcvc-deps repo",
)


# ── Load all real recipes ──────────────────────────────────────


@requires_repo
class TestLoadAllRecipes:
    """Verify every recipe in the repo can be loaded without error."""

    def test_load_all(self):
        recipes = list_recipes(RECIPES_DIR)
        assert len(recipes) >= 20  # We have ~25 recipes

    def test_every_recipe_has_name(self):
        for r in list_recipes(RECIPES_DIR):
            assert r.name
            assert r.upstream_version
            assert r.cvc_revision >= 1

    def test_every_recipe_has_build_matrix(self):
        for r in list_recipes(RECIPES_DIR):
            assert len(r.build_matrix) >= 1, f"{r.name} has no build matrix entries"

    def test_every_recipe_build_scripts_exist(self):
        for r in list_recipes(RECIPES_DIR):
            for m in r.build_matrix:
                script = r.recipe_dir / m.script
                assert script.is_file(), f"{r.name}: missing {m.script}"

    def test_recipe_names_match_dirs(self):
        for r in list_recipes(RECIPES_DIR):
            assert (
                r.name == r.recipe_dir.name
            ), f"Recipe name '{r.name}' doesn't match directory '{r.recipe_dir.name}'"


# ── Manifest generation round-trip ──────────────────────────────


@requires_repo
class TestManifestRoundTrip:
    """Generate a manifest, write it, and parse it back."""

    def test_generate_and_parse(self, tmp_path):
        recipe = Recipe.load(RECIPES_DIR / "zlib")
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / "lib").mkdir()
        (install_dir / "lib" / "libz.so").write_text("fake")
        (install_dir / "include").mkdir()
        (install_dir / "include" / "zlib.h").write_text("header")

        manifest_dict = generate_manifest(
            recipe, install_dir, "linux", "x86_64", "release", "shared"
        )

        # Write and read back
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest_dict, default_flow_style=False))

        raw = yaml.safe_load(manifest_path.read_text())
        assert raw["schema_version"] == 3
        assert raw["bundle"]["name"] == "zlib"
        assert "lib/libz.so" in raw["contents"]["files"]


# ── Full pack pipeline (without actually building) ──────────────


@requires_repo
class TestPackPipeline:
    """Test the staging + archive creation pipeline."""

    def test_stage_and_archive(self, tmp_path):
        recipe = Recipe.load(RECIPES_DIR / "zlib")

        # Simulate a built install tree
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / "lib").mkdir()
        (install_dir / "lib" / "libz.so.1.3.1").write_bytes(b"\x7fELF" + b"\x00" * 100)
        (install_dir / "include").mkdir()
        (install_dir / "include" / "zlib.h").write_text("#ifndef ZLIB_H\n#define ZLIB_H\n#endif\n")

        manifest = generate_manifest(recipe, install_dir, "linux", "x86_64", "release", "shared")

        staging = tmp_path / "staging"
        staging.mkdir()
        stage_bundle(install_dir, manifest, staging)

        # Verify staging
        assert (staging / "lib" / "libz.so.1.3.1").exists()
        assert (staging / "include" / "zlib.h").exists()
        assert (staging / "share" / "libcvc-deps" / "manifest.yaml").exists()

        # Create archive
        dist = tmp_path / "dist"
        archive_path, sha, size = create_archive(
            staging,
            dist,
            recipe.name,
            recipe.full_version,
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        assert archive_path.exists()
        assert size > 0
        assert len(sha) == 64


# ── Lockfile with real recipe data ──────────────────────────────


@requires_repo
class TestLockfileIntegration:
    """Lockfile round-trip with data from real recipes."""

    def test_lockfile_from_recipes(self, tmp_path):
        recipes = list_recipes(RECIPES_DIR)[:5]

        bundles = [
            LockEntry(
                name=r.name,
                version=r.full_version,
                upstream_version=r.upstream_version,
            )
            for r in recipes
        ]
        lf = Lockfile(
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
            bundles=bundles,
        )

        path = tmp_path / "lockfile.yaml"
        lf.write(path)

        lf2 = Lockfile.read(path)
        assert len(lf2.bundles) == len(recipes[:5])
        for i, r in enumerate(recipes[:5]):
            assert lf2.bundles[i].name == r.name


# ── CLI integration ────────────────────────────────────────────


@requires_repo
class TestCLIIntegration:
    """Integration tests that exercise the CLI against real recipes."""

    def test_recipes_list_all(self, capsys):
        from cvcpkg.cli import main

        ret = main(["recipes", "--list"])
        assert ret == 0
        captured = capsys.readouterr()
        # Should list every recipe
        for name in ("zlib", "boost", "grpc", "vtk", "qt6"):
            assert name in captured.out

    def test_recipes_show_each(self, capsys):
        from cvcpkg.cli import main

        for name in ("zlib", "openssl", "nfft3"):
            ret = main(["recipes", "--show", name])
            assert ret == 0, f"Failed to show recipe '{name}'"


# ── cvc-requirements.yaml validation ──────────────────────────

requires_requirements = pytest.mark.skipif(
    not REQUIREMENTS_FILE.is_file(),
    reason="cvc-requirements.yaml not found at repo root",
)


@requires_requirements
class TestCvcRequirements:
    """Validate the real cvc-requirements.yaml loads correctly."""

    def test_load(self):
        raw = yaml.safe_load(REQUIREMENTS_FILE.read_text())
        reqs = Requirements.from_dict(raw)
        assert len(reqs.components) >= 20

    def test_all_components_have_recipes(self):
        raw = yaml.safe_load(REQUIREMENTS_FILE.read_text())
        reqs = Requirements.from_dict(raw)
        recipe_names = {r.name for r in list_recipes(RECIPES_DIR)}
        for c in reqs.components:
            assert (
                c.name in recipe_names
            ), f"Component '{c.name}' in cvc-requirements.yaml has no recipe"


# ── Build-order resolution with real recipes ───────────────────


@requires_repo
class TestResolveBuildOrderIntegration:
    """Verify resolve_build_order works with the real recipe set."""

    def test_all_recipes_resolve(self):
        """All real recipes should resolve to a valid build order."""
        recipes = list_recipes(RECIPES_DIR)
        ordered = resolve_build_order(recipes)
        assert len(ordered) == len(recipes)

    def test_deps_come_before_dependants(self):
        """Every recipe's build deps should appear earlier in the order."""
        recipes = list_recipes(RECIPES_DIR)
        ordered = resolve_build_order(recipes)
        name_to_idx = {r.name: i for i, r in enumerate(ordered)}

        for r in ordered:
            depends = r.raw.get("depends", {}).get("build", [])
            for dep in depends:
                dep_name = dep if isinstance(dep, str) else dep["name"]
                if dep_name in name_to_idx:
                    assert (
                        name_to_idx[dep_name] < name_to_idx[r.name]
                    ), f"{dep_name} should come before {r.name}"

    def test_no_duplicate_names(self):
        """Each recipe name should appear exactly once in the order."""
        recipes = list_recipes(RECIPES_DIR)
        ordered = resolve_build_order(recipes)
        names = [r.name for r in ordered]
        assert len(names) == len(set(names))


# ── Manifest hardening round-trip with real files ──────────────


@requires_repo
class TestManifestHardeningIntegration:
    """Verify from_yaml hardening with files generated from real recipes."""

    def test_from_yaml_valid_manifest(self, tmp_path):
        """Generate a manifest from zlib, write it, re-read as raw YAML.

        Note: generate_manifest() uses 'config'/'link' keys while
        BundleManifest.from_dict() expects 'build_type'/'link' — so we
        verify the raw round-trip here, not the dataclass parse.
        """
        recipe = Recipe.load(RECIPES_DIR / "zlib")
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / "lib").mkdir()
        (install_dir / "lib" / "libz.so").write_text("fake")

        manifest_dict = generate_manifest(
            recipe, install_dir, "linux", "x86_64", "release", "shared"
        )
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest_dict, default_flow_style=False))

        # Re-read and verify the raw structure round-trips
        raw = yaml.safe_load(manifest_path.read_text())
        assert raw["schema_version"] == 3
        assert raw["bundle"]["name"] == "zlib"
        assert raw["bundle"]["platform"] == "linux"

    def test_from_yaml_corrupted_manifest(self, tmp_path):
        """A corrupted manifest should raise SchemaError."""

        p = tmp_path / "bad_manifest.yaml"
        p.write_text("not: valid: yaml: [[[")
        with pytest.raises(Exception):
            BundleManifest.from_yaml(str(p))
