"""Tests for cvcpkg.builder — recipe loading, building, packaging."""

from __future__ import annotations

import hashlib
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
    Recipe,
    RecipeError,
    SourceSpec,
    _file_list,
    _select_matrix_entry,
    _sha256_file,
    _total_size,
    apply_patches,
    create_archive,
    fetch_source,
    generate_manifest,
    list_recipes,
    load_all_recipes,
    resolve_build_order,
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

    def test_manifest_platform_filtered_deps(self, tmp_path):
        """Platform-conditional deps are filtered in the manifest."""
        recipe_dict = {
            **MINIMAL_RECIPE,
            "depends": {
                "build": [
                    {"name": "openblas", "version": ">=0.3", "platforms": ["linux", "macos"]},
                    {"name": "clapack", "version": ">=3.2", "platforms": ["windows"]},
                    {"name": "zlib", "version": "^1.3"},
                ]
            },
        }
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)

        install_dir = tmp_path / "install"
        install_dir.mkdir()

        m = generate_manifest(r, install_dir, "linux", "x86_64", "release", "shared")
        deps = m["depends"]
        assert len(deps) == 2
        assert deps[0] == {"name": "openblas", "version": ">=0.3"}
        assert deps[1] == {"name": "zlib", "version": "^1.3"}

        # Windows should get clapack, not openblas
        m2 = generate_manifest(r, install_dir, "windows", "x86_64", "release", "static")
        deps2 = m2["depends"]
        assert len(deps2) == 2
        assert deps2[0] == {"name": "clapack", "version": ">=3.2"}
        assert deps2[1] == {"name": "zlib", "version": "^1.3"}

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


# ── Hardening: patch path traversal ────────────────────────────


class TestPatchTraversal:
    """Verify that apply_patches rejects patches that escape the recipe dir."""

    def test_traversal_rejected(self, tmp_path):
        """Patches referencing ../../ should raise RecipeError."""
        recipe_dir = tmp_path / "recipes" / "evil"
        recipe_dir.mkdir(parents=True)
        _write_recipe(
            recipe_dir,
            {
                **MINIMAL_RECIPE,
                "patches": ["../../etc/passwd"],
            },
        )
        recipe = Recipe.load(recipe_dir)

        source_dir = tmp_path / "src"
        source_dir.mkdir()

        with pytest.raises(RecipeError, match="escapes recipe directory"):
            apply_patches(recipe, source_dir)

    def test_normal_patch_allowed(self, tmp_path):
        """A patch file within the recipe dir should pass the traversal check."""
        recipe_dir = tmp_path / "recipes" / "good"
        recipe_dir.mkdir(parents=True)

        # Create a valid (no-op) patch file
        patch_file = recipe_dir / "fix.patch"
        patch_file.write_text("")

        _write_recipe(
            recipe_dir,
            {
                **MINIMAL_RECIPE,
                "patches": ["fix.patch"],
            },
        )
        recipe = Recipe.load(recipe_dir)

        source_dir = tmp_path / "src"
        source_dir.mkdir()

        # An empty patch file may succeed (no-op) or fail, but the key
        # assertion is that it does NOT raise "escapes recipe directory".
        try:
            apply_patches(recipe, source_dir)
        except RecipeError as e:
            assert "escapes recipe directory" not in str(e)

    def test_patch_not_found(self, tmp_path):
        """A patch file that doesn't exist should raise RecipeError."""
        recipe_dir = tmp_path / "recipes" / "missing"
        recipe_dir.mkdir(parents=True)
        _write_recipe(
            recipe_dir,
            {
                **MINIMAL_RECIPE,
                "patches": ["nonexistent.patch"],
            },
        )
        recipe = Recipe.load(recipe_dir)

        source_dir = tmp_path / "src"
        source_dir.mkdir()

        with pytest.raises(RecipeError, match="not found"):
            apply_patches(recipe, source_dir)


# ── Hardening: resolve_build_order ─────────────────────────────


class TestResolveBuildOrder:
    """Topological sort of recipes by dependency."""

    def _make_recipe(self, tmp_path, name, deps=None):
        """Create a minimal recipe with optional build dependencies."""
        recipe_dir = tmp_path / "recipes" / name
        recipe_dir.mkdir(parents=True, exist_ok=True)
        d = {
            **MINIMAL_RECIPE,
            "recipe": {**MINIMAL_RECIPE["recipe"], "name": name},
        }
        if deps:
            d["depends"] = {"build": deps}
        _write_recipe(recipe_dir, d)
        return Recipe.load(recipe_dir)

    def test_no_deps(self, tmp_path):
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b")
        order = resolve_build_order([a, b])
        names = [r.name for r in order]
        assert set(names) == {"a", "b"}

    def test_linear_deps(self, tmp_path):
        """c depends on b, b depends on a → order is a, b, c."""
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b", deps=["a"])
        c = self._make_recipe(tmp_path, "c", deps=["b"])
        order = resolve_build_order([c, b, a])
        names = [r.name for r in order]
        assert names.index("a") < names.index("b")
        assert names.index("b") < names.index("c")

    def test_diamond_deps(self, tmp_path):
        """d depends on b and c; both depend on a."""
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b", deps=["a"])
        c = self._make_recipe(tmp_path, "c", deps=["a"])
        d = self._make_recipe(tmp_path, "d", deps=["b", "c"])
        order = resolve_build_order([d, c, b, a])
        names = [r.name for r in order]
        assert names.index("a") < names.index("b")
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("d")
        assert names.index("c") < names.index("d")

    def test_cycle_detected(self, tmp_path):
        """Cycle a→b→a should raise RecipeError."""
        a = self._make_recipe(tmp_path, "a", deps=["b"])
        b = self._make_recipe(tmp_path, "b", deps=["a"])
        with pytest.raises(RecipeError, match="cycle"):
            resolve_build_order([a, b])

    def test_missing_dep(self, tmp_path):
        """Dependency on a recipe not in the list is silently skipped (assumed pre-installed)."""
        a = self._make_recipe(tmp_path, "a", deps=["missing"])
        order = resolve_build_order([a])
        names = [r.name for r in order]
        assert names == ["a"]

    def test_dict_style_deps(self, tmp_path):
        """Dependencies can also be specified as dicts with a 'name' key."""
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b", deps=[{"name": "a"}])
        order = resolve_build_order([b, a])
        names = [r.name for r in order]
        assert names.index("a") < names.index("b")

    def test_platform_conditional_deps_included(self, tmp_path):
        """Dep with matching platform is included in build order."""
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b", deps=[{"name": "a", "platforms": ["linux"]}])
        order = resolve_build_order([b, a], platform="linux")
        names = [r.name for r in order]
        assert names.index("a") < names.index("b")

    def test_platform_conditional_deps_excluded(self, tmp_path):
        """Dep with non-matching platform is excluded from build order."""
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b", deps=[{"name": "a", "platforms": ["windows"]}])
        # 'a' is not required by 'b' on linux, so order is unlinked
        order = resolve_build_order([b, a], platform="linux")
        names = [r.name for r in order]
        assert "a" in names and "b" in names

    def test_platform_conditional_deps_no_filter(self, tmp_path):
        """Without platform filter, all deps are included regardless of platforms field."""
        a = self._make_recipe(tmp_path, "a")
        b = self._make_recipe(tmp_path, "b", deps=[{"name": "a", "platforms": ["windows"]}])
        order = resolve_build_order([b, a])
        names = [r.name for r in order]
        assert names.index("a") < names.index("b")

    def test_platform_conditional_mixed_deps(self, tmp_path):
        """Mix of platform-specific and unconditional deps."""
        a = self._make_recipe(tmp_path, "a")
        c = self._make_recipe(tmp_path, "c")
        b = self._make_recipe(
            tmp_path,
            "b",
            deps=[
                {"name": "a", "platforms": ["linux", "macos"]},
                {"name": "c", "platforms": ["windows"]},
            ],
        )
        order = resolve_build_order([b, c, a], platform="linux")
        names = [r.name for r in order]
        assert names.index("a") < names.index("b")
        # c is in the list (it's a recipe) but not required by b on linux


# ── Hardening: fetch_tarball specific exceptions ───────────────


class TestFetchTarballExceptions:
    """Verify _fetch_tarball catches specific urllib exceptions."""

    def test_no_urls_raises(self, tmp_path):
        """Source with no URL or mirror should raise RecipeError."""
        source = SourceSpec(type="tarball", url="", mirror="")
        with pytest.raises(RecipeError, match="no URL specified"):
            from cvcpkg.builder import _fetch_tarball

            _fetch_tarball(source, tmp_path)

    def test_bad_url_raises(self, tmp_path):
        """An unreachable URL should raise RecipeError (mocked)."""
        import urllib.error

        source = SourceSpec(
            type="tarball",
            url="http://example.invalid/nonexistent.tar.gz",
        )
        with patch(
            "urllib.request.urlretrieve",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(RecipeError, match="failed to download"):
                from cvcpkg.builder import _fetch_tarball

                _fetch_tarball(source, tmp_path)


# ── Hardening: run_build interpreter check ─────────────────────


class TestRunBuildInterpreter:
    """Verify run_build validates interpreter availability."""

    def test_unknown_script_suffix(self, tmp_path):
        """A script with an unknown suffix should raise BuildError."""
        from cvcpkg.builder import run_build

        recipe_dir = tmp_path / "recipes" / "bad"
        recipe_dir.mkdir(parents=True)
        d = {
            **MINIMAL_RECIPE,
            "build": {
                "matrix": [
                    {"platform": "linux", "script": "build.rb"},
                ],
            },
        }
        _write_recipe(recipe_dir, d)
        # Create the script file so we pass the "not found" check
        (recipe_dir / "build.rb").write_text("#!/usr/bin/env ruby\n")

        recipe = Recipe.load(recipe_dir)
        ctx = BuildContext(
            recipe=recipe,
            platform="linux",
            config="release",
            link="shared",
            prefix=tmp_path / "prefix",
            source_dir=tmp_path / "src",
            build_dir=tmp_path / "build",
            install_dir=tmp_path / "install",
            work_dir=tmp_path / "work",
        )
        with pytest.raises(BuildError, match="Unknown script type"):
            run_build(ctx)


# ── Tags ────────────────────────────────────────────────────────


class TestRecipeTags:
    def test_load_recipe_with_tags(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE}
        recipe_dict["recipe"] = {**recipe_dict["recipe"], "tags": ["math", "utils"]}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        assert r.tags == ["math", "utils"]

    def test_load_recipe_without_tags(self, tmp_path):
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        r = Recipe.load(recipe_dir)
        assert r.tags == []

    def test_load_recipe_empty_tags(self, tmp_path):
        recipe_dict = {**MINIMAL_RECIPE}
        recipe_dict["recipe"] = {**recipe_dict["recipe"], "tags": []}
        recipe_dir = tmp_path / "recipes" / "testpkg"
        _write_recipe(recipe_dir, recipe_dict)
        r = Recipe.load(recipe_dir)
        assert r.tags == []


# ── load_all_recipes ────────────────────────────────────────────


class TestLoadAllRecipes:
    def _make_recipe(self, recipes_dir, name, **overrides):
        d = {**MINIMAL_RECIPE}
        d["recipe"] = {**d["recipe"], "name": name, **overrides}
        d["source"] = {"type": "vendored", "path": f"third-party/{name}"}
        recipe_dir = recipes_dir / name
        _write_recipe(recipe_dir, d)

    def test_single_dir(self, tmp_path):
        rd = tmp_path / "recipes"
        self._make_recipe(rd, "alpha")
        self._make_recipe(rd, "beta")
        result = load_all_recipes([rd])
        names = [r.name for r in result]
        assert names == ["alpha", "beta"]

    def test_multiple_dirs_merge(self, tmp_path):
        rd1 = tmp_path / "dir1"
        rd2 = tmp_path / "dir2"
        self._make_recipe(rd1, "alpha")
        self._make_recipe(rd2, "beta")
        result = load_all_recipes([rd1, rd2])
        names = [r.name for r in result]
        assert names == ["alpha", "beta"]

    def test_later_dir_overrides(self, tmp_path, capsys):
        rd1 = tmp_path / "dir1"
        rd2 = tmp_path / "dir2"
        self._make_recipe(rd1, "alpha", cvc_revision=1)
        self._make_recipe(rd2, "alpha", cvc_revision=2)
        result = load_all_recipes([rd1, rd2])
        assert len(result) == 1
        assert result[0].cvc_revision == 2
        captured = capsys.readouterr()
        assert "overrides" in captured.out

    def test_empty_dirs_raises(self):
        with pytest.raises(RecipeError, match="No recipe directories"):
            load_all_recipes([])

    def test_three_dirs_overlay(self, tmp_path):
        rd1 = tmp_path / "base"
        rd2 = tmp_path / "overlay"
        rd3 = tmp_path / "local"
        self._make_recipe(rd1, "alpha")
        self._make_recipe(rd1, "beta")
        self._make_recipe(rd2, "gamma")
        self._make_recipe(rd3, "beta", cvc_revision=5)
        result = load_all_recipes([rd1, rd2, rd3])
        names = [r.name for r in result]
        assert names == ["alpha", "beta", "gamma"]
        beta = [r for r in result if r.name == "beta"][0]
        assert beta.cvc_revision == 5
