"""Tests for cvcpkg.builder — recipe loading, building, packaging."""

from __future__ import annotations

import hashlib
import os
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from cvcpkg.builder import (
    BuildContext,
    BuildError,
    MatrixEntry,
    PackError,
    Recipe,
    RecipeError,
    SourceSpec,
    _archive_tar_gz,
    _archive_zip,
    _file_list,
    _select_matrix_entry,
    _sha256_file,
    _total_size,
    apply_patches,
    create_archive,
    fetch_source,
    generate_manifest,
    list_recipes,
    stage_bundle,
)

# ── Helpers ─────────────────────────────────────────────────────


def _write_recipe(recipe_dir: Path, recipe_dict: dict) -> Path:
    """Write a recipe.yaml and return its path."""
    recipe_dir.mkdir(parents=True, exist_ok=True)
    p = recipe_dir / "recipe.yaml"
    p.write_text(yaml.dump(recipe_dict, default_flow_style=False))
    return p


MINIMAL_RECIPE = {
    "schema_version": 1,
    "recipe": {
        "name": "testpkg",
        "upstream_version": "1.0.0",
        "cvc_revision": 1,
    },
    "source": {"type": "vendored", "path": "third-party/testpkg"},
    "patches": [],
    "build": {
        "matrix": [
            {"platform": "linux", "script": "build.sh"},
        ],
    },
    "package": {
        "files": ["lib/*", "include/*"],
        "cmake_packages": [],
    },
}


# ── SourceSpec ──────────────────────────────────────────────────


class TestSourceSpec:
    def test_from_dict_tarball(self):
        s = SourceSpec.from_dict(
            {
                "type": "tarball",
                "url": "https://example.com/pkg-1.0.tar.gz",
                "sha256": "a" * 64,
            }
        )
        assert s.type == "tarball"
        assert s.url == "https://example.com/pkg-1.0.tar.gz"
        assert s.sha256 == "a" * 64

    def test_from_dict_vendored(self):
        s = SourceSpec.from_dict({"type": "vendored", "path": "third-party/levmar"})
        assert s.type == "vendored"
        assert s.path == "third-party/levmar"

    def test_from_dict_vcpkg(self):
        s = SourceSpec.from_dict({"type": "vcpkg", "port": "grpc", "triplet": "x64-windows"})
        assert s.port == "grpc"
        assert s.triplet == "x64-windows"

    def test_defaults(self):
        s = SourceSpec.from_dict({"type": "tarball"})
        assert s.url == ""
        assert s.mirror == ""
        assert s.sha256 == ""
        assert s.strip_components == 1


# ── MatrixEntry ─────────────────────────────────────────────────


class TestMatrixEntry:
    def test_from_dict(self):
        m = MatrixEntry.from_dict({"platform": "linux", "script": "build.sh"})
        assert m.platform == "linux"
        assert m.script == "build.sh"
        assert m.env == {}

    def test_with_env(self):
        m = MatrixEntry.from_dict(
            {
                "platform": "windows",
                "script": "build.ps1",
                "env": {"FOO": "bar"},
            }
        )
        assert m.env == {"FOO": "bar"}


# ── Recipe loading ──────────────────────────────────────────────


class TestRecipeLoad:
    def test_load_minimal(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        assert r.name == "testpkg"
        assert r.upstream_version == "1.0.0"
        assert r.cvc_revision == 1
        assert r.full_version == "1.0.0+cvc.1"

    def test_load_missing_recipe_yaml(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "nope"
        recipe_dir.mkdir(parents=True)
        with pytest.raises(RecipeError, match="recipe.yaml not found"):
            Recipe.load(recipe_dir)

    def test_load_with_patches(self, tmp_path):
        recipe_dict = {
            **MINIMAL_RECIPE,
            "patches": ["fix-build.patch"],
        }
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        assert r.patches == ["fix-build.patch"]

    def test_load_with_test_script(self, tmp_path):
        recipe_dict = {
            **MINIMAL_RECIPE,
            "test": {"script": "test.sh"},
        }
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        assert r.test_script == "test.sh"

    def test_full_version(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE}
        recipe_dict["recipe"] = {**recipe_dict["recipe"], "cvc_revision": 3}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        assert r.full_version == "1.0.0+cvc.3"

    def test_load_multiple_platforms(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE}
        recipe_dict["build"] = {
            "matrix": [
                {"platform": "linux", "script": "build.sh"},
                {"platform": "macos", "script": "build.sh"},
                {"platform": "windows", "script": "build.ps1"},
            ],
        }
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        assert len(r.build_matrix) == 3


# ── Matrix selection ────────────────────────────────────────────


class TestSelectMatrixEntry:
    def test_selects_linux(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        m = _select_matrix_entry(r, "linux")
        assert m.platform == "linux"

    def test_missing_platform_raises(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        with pytest.raises(RecipeError, match="No build matrix entry for platform 'windows'"):
            _select_matrix_entry(r, "windows")


# ── Source fetching ─────────────────────────────────────────────


class TestFetchSource:
    def test_vendored_source(self, tmp_path):
        # Set up vendored source tree
        repo_root = tmp_path
        vendored = repo_root / "third-party" / "testpkg"
        vendored.mkdir(parents=True)
        (vendored / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)")

        recipe_dir = repo_root / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        src = fetch_source(r, work_dir)
        assert src.is_dir()
        assert (src / "CMakeLists.txt").exists()

    def test_vendored_missing_raises(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        with pytest.raises(RecipeError, match="Vendored source not found"):
            fetch_source(r, work_dir)

    def test_vcpkg_dummy_source(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE, "source": {"type": "vcpkg", "port": "zlib"}}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        src = fetch_source(r, work_dir)
        assert src.is_dir()

    def test_git_source_not_implemented(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE, "source": {"type": "git"}}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        with pytest.raises(RecipeError, match="not yet implemented"):
            fetch_source(r, work_dir)

    def test_unknown_source_type(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE, "source": {"type": "magic"}}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        with pytest.raises(RecipeError, match="Unknown source type"):
            fetch_source(r, work_dir)


# ── Patch application ──────────────────────────────────────────


class TestApplyPatches:
    def test_no_patches_noop(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        # Should not raise
        apply_patches(r, tmp_path / "src")

    def test_missing_patch_raises(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE, "patches": ["no-such.patch"]}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        with pytest.raises(RecipeError, match="Patch file not found"):
            apply_patches(r, tmp_path / "src")


# ── _sha256_file ────────────────────────────────────────────────


class TestSha256File:
    def test_correct_hash(self, tmp_path):
        content = b"test content for sha256"
        p = tmp_path / "file"
        p.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _sha256_file(p) == expected


# ── _file_list ──────────────────────────────────────────────────


class TestFileList:
    def test_lists_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b")

        files = _file_list(tmp_path)
        assert "a.txt" in files
        assert "sub/b.txt" in files

    def test_empty_dir(self, tmp_path):
        assert _file_list(tmp_path) == []

    def test_sorted(self, tmp_path):
        (tmp_path / "z.txt").write_text("z")
        (tmp_path / "a.txt").write_text("a")
        files = _file_list(tmp_path)
        assert files == sorted(files)


# ── _total_size ─────────────────────────────────────────────────


class TestTotalSize:
    def test_counts_bytes(self, tmp_path):
        (tmp_path / "a").write_bytes(b"hello")
        (tmp_path / "b").write_bytes(b"world!")
        assert _total_size(tmp_path) == 11


# ── generate_manifest ──────────────────────────────────────────


class TestGenerateManifest:
    def test_basic_manifest(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)

        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / "lib").mkdir()
        (install_dir / "lib" / "libtest.so").write_text("lib")
        (install_dir / "include").mkdir()
        (install_dir / "include" / "test.h").write_text("header")

        m = generate_manifest(r, install_dir, "linux", "x86_64", "release", "shared")
        assert m["schema_version"] == 3
        assert m["bundle"]["name"] == "testpkg"
        assert m["bundle"]["version"] == "1.0.0+cvc.1"
        assert m["bundle"]["platform"] == "linux"
        assert m["bundle"]["config"] == "release"
        assert "lib/libtest.so" in m["contents"]["files"]
        assert "include/test.h" in m["contents"]["files"]

    def test_manifest_with_deps(self, tmp_path):
        recipe_dict = {
            **MINIMAL_RECIPE,
            "depends": {"build": [{"name": "zlib", "version": "^1.3"}, "fftw3"]},
        }
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)

        install_dir = tmp_path / "install"
        install_dir.mkdir()

        m = generate_manifest(r, install_dir, "linux", "x86_64", "release", "shared")
        deps = m["depends"]
        assert len(deps) == 2
        assert deps[0] == {"name": "zlib", "version": "^1.3"}
        assert deps[1] == {"name": "fftw3"}

    def test_manifest_recipe_sha256(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        install_dir = tmp_path / "install"
        install_dir.mkdir()

        m = generate_manifest(r, install_dir, "linux", "x86_64", "release", "shared")
        assert len(m["meta"]["recipe_sha256"]) == 64
        assert m["meta"]["built_at"]


# ── stage_bundle ────────────────────────────────────────────────


class TestStageBundle:
    def test_stages_files_and_manifest(self, tmp_path):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / "lib").mkdir()
        (install_dir / "lib" / "libtest.so").write_text("lib")

        manifest = {"schema_version": 3, "bundle": {"name": "test"}}
        staging = tmp_path / "staging"
        staging.mkdir()

        stage_bundle(install_dir, manifest, staging)
        assert (staging / "lib" / "libtest.so").exists()
        assert (staging / "share" / "libcvc-deps" / "manifest.yaml").exists()

        manifest_content = yaml.safe_load(
            (staging / "share" / "libcvc-deps" / "manifest.yaml").read_text()
        )
        assert manifest_content["bundle"]["name"] == "test"


# ── create_archive ──────────────────────────────────────────────


class TestCreateArchive:
    def _make_staging(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "lib").mkdir()
        (staging / "lib" / "libtest.so").write_text("library content")
        (staging / "include").mkdir()
        (staging / "include" / "test.h").write_text("header content")
        return staging

    def test_tar_gz_on_linux(self, tmp_path):
        staging = self._make_staging(tmp_path)
        out = tmp_path / "dist"
        path, sha, size = create_archive(
            staging,
            out,
            "testpkg",
            "1.0.0+cvc.1",
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        assert path.suffix == ".gz"
        assert path.exists()
        assert len(sha) == 64
        assert size > 0

        # Verify contents
        with tarfile.open(path) as tf:
            names = tf.getnames()
            assert "lib/libtest.so" in names
            assert "include/test.h" in names

    def test_zip_on_windows(self, tmp_path):
        staging = self._make_staging(tmp_path)
        out = tmp_path / "dist"
        path, sha, size = create_archive(
            staging,
            out,
            "testpkg",
            "1.0.0+cvc.1",
            "windows",
            "x86_64",
            "release",
            "shared",
        )
        assert path.suffix == ".zip"
        assert path.exists()

        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "lib/libtest.so" in names
            assert "include/test.h" in names

    def test_deterministic_tar(self, tmp_path):
        staging = self._make_staging(tmp_path)
        out1 = tmp_path / "dist1"
        out2 = tmp_path / "dist2"
        _, sha1, _ = create_archive(
            staging, out1, "p", "1.0", "linux", "x86_64", "release", "shared"
        )
        _, sha2, _ = create_archive(
            staging, out2, "p", "1.0", "linux", "x86_64", "release", "shared"
        )
        assert sha1 == sha2

    def test_deterministic_zip(self, tmp_path):
        staging = self._make_staging(tmp_path)
        out1 = tmp_path / "dist1"
        out2 = tmp_path / "dist2"
        _, sha1, _ = create_archive(
            staging, out1, "p", "1.0", "windows", "x86_64", "release", "shared"
        )
        _, sha2, _ = create_archive(
            staging, out2, "p", "1.0", "windows", "x86_64", "release", "shared"
        )
        assert sha1 == sha2

    def test_archive_stem_format(self, tmp_path):
        staging = self._make_staging(tmp_path)
        out = tmp_path / "dist"
        path, _, _ = create_archive(
            staging,
            out,
            "zlib",
            "1.3.1+cvc.1",
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        assert path.name == "zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz"


# ── list_recipes (against the real recipes/ dir) ─────────────────


class TestListRecipes:
    def test_list_synthetic(self, tmp_path):
        """List recipes from a synthetic recipes dir."""
        recipes_dir = tmp_path / "recipes"
        common = recipes_dir / "_common"
        common.mkdir(parents=True)
        (common / "env-linux.sh").write_text("#!/bin/bash\n")

        for name in ("alpha", "beta"):
            rd = recipes_dir / name
            rd.mkdir()
            _write_recipe(
                rd,
                {
                    **MINIMAL_RECIPE,
                    "recipe": {**MINIMAL_RECIPE["recipe"], "name": name},
                },
            )

        recipes = list_recipes(recipes_dir)
        names = [r.name for r in recipes]
        assert "alpha" in names
        assert "beta" in names
        assert "_common" not in names

    def test_list_skips_non_recipe_dirs(self, tmp_path):
        recipes_dir = tmp_path / "recipes"
        common = recipes_dir / "_common"
        common.mkdir(parents=True)
        (common / "env-linux.sh").write_text("#!/bin/bash\n")

        # A file, not a directory
        (recipes_dir / "readme.txt").write_text("not a recipe")

        # A dir without recipe.yaml
        (recipes_dir / "empty").mkdir()

        recipes = list_recipes(recipes_dir)
        assert len(recipes) == 0
