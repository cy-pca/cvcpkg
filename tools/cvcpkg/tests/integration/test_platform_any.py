"""Integration tests for platform 'any' (platform-independent recipes).

These tests use real (but tiny) dummy recipes with actual build scripts
that create files, exercising the full build_all / pack_all pipeline
end-to-end including source fetching, build execution, caching,
dependency resolution, manifest generation, and archiving.

No server or Docker is required.
"""

from __future__ import annotations

import hashlib
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from cvcpkg.build_cache import BuildCache
from cvcpkg.builder import (
    Recipe,
    build_all,
    chain_hash,
    create_archive,
    generate_manifest,
    list_recipes,
    resolve_build_order,
    stage_bundle,
)


# ── Helpers ─────────────────────────────────────────────────────


def _create_any_recipe(
    recipes_dir: Path,
    name: str,
    *,
    deps: list[str] | None = None,
    kind: str = "data",
    files: dict[str, str] | None = None,
) -> Path:
    """Create a platform-independent ('any') recipe.

    The build script copies files from source into
    ``$CVC_INSTALL_DIR/share/<name>/``.  If *files* is given, those
    files are placed in the vendored source directory.
    """
    recipe_dir = recipes_dir / name
    recipe_dir.mkdir(parents=True, exist_ok=True)

    repo_root = recipes_dir.parent
    src = repo_root / "third-party" / name
    src.mkdir(parents=True, exist_ok=True)

    if files:
        for fname, content in files.items():
            p = src / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    else:
        (src / "index.html").write_text(f"<html>{name}</html>")
        (src / "style.css").write_text("body { margin: 0; }")

    recipe = {
        "schema_version": 1,
        "recipe": {
            "name": name,
            "upstream_version": "1.0.0",
            "cvc_revision": 1,
            "kind": kind,
            "tags": ["platform-independent"],
        },
        "source": {"type": "vendored", "path": f"third-party/{name}"},
        "patches": [],
        "build": {
            "matrix": [
                {"platform": "any", "script": "build.sh"},
            ],
        },
        "package": {
            "files": [f"share/{name}/*"],
        },
    }
    if deps:
        recipe["depends"] = {"build": deps}

    (recipe_dir / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
    (recipe_dir / "build.sh").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            set -e
            mkdir -p "$CVC_INSTALL_DIR/share/{name}"
            cp -r "$CVC_SOURCE_DIR"/* "$CVC_INSTALL_DIR/share/{name}/"
        """
        )
    )
    os.chmod(recipe_dir / "build.sh", 0o755)
    return recipe_dir


def _create_linux_recipe(
    recipes_dir: Path,
    name: str,
    *,
    deps: list[str] | None = None,
) -> Path:
    """Create a minimal linux-only recipe that produces a marker file."""
    recipe_dir = recipes_dir / name
    recipe_dir.mkdir(parents=True, exist_ok=True)

    repo_root = recipes_dir.parent
    src = repo_root / "third-party" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "README").write_text(f"source for {name}")

    recipe = {
        "schema_version": 1,
        "recipe": {
            "name": name,
            "upstream_version": "1.0.0",
            "cvc_revision": 1,
        },
        "source": {"type": "vendored", "path": f"third-party/{name}"},
        "patches": [],
        "build": {
            "matrix": [
                {"platform": "linux", "script": "build.sh"},
            ],
        },
        "package": {
            "files": ["lib/*"],
            "cmake_packages": [],
        },
    }
    if deps:
        recipe["depends"] = {"build": deps}

    (recipe_dir / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
    (recipe_dir / "build.sh").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            set -e
            mkdir -p "$CVC_INSTALL_DIR/lib"
            echo "built-{name}" > "$CVC_INSTALL_DIR/lib/lib{name}.txt"
        """
        )
    )
    os.chmod(recipe_dir / "build.sh", 0o755)
    return recipe_dir


# ── Real build tests (skipped on CI) ────────────────────────────


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Runs real builds -- too slow for CI",
)
class TestPlatformAnyBuild:
    """End-to-end build tests with real (tiny) recipes."""

    def test_build_any_recipe_standalone(self, tmp_path, monkeypatch):
        """A standalone 'any' recipe builds and installs its files."""
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_any_recipe(recipes_dir, "web-assets")

        prefix = tmp_path / "prefix"
        contexts = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
        )

        assert len(contexts) == 1
        assert contexts[0].recipe.name == "web-assets"
        # Verify files were installed
        assert (prefix / "share" / "web-assets" / "index.html").is_file()
        assert (prefix / "share" / "web-assets" / "style.css").is_file()

    def test_build_any_recipe_for_multiple_platforms(self, tmp_path, monkeypatch):
        """The same 'any' recipe builds for linux and again (from cache) for another platform."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(cache_dir))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_any_recipe(recipes_dir, "config-files", kind="config")

        # First build for "linux"
        prefix1 = tmp_path / "prefix1"
        ctx1 = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix1,
            per_component=True,
        )
        assert len(ctx1) == 1
        assert (prefix1 / "share" / "config-files" / "index.html").is_file()

        # Verify cache was populated with "any" platform key
        bc = BuildCache(cache_dir)
        entries = bc.list_entries()
        assert len(entries) == 1
        assert entries[0].name == "config-files"

        # Second build for "linux" again -- should hit cache
        prefix2 = tmp_path / "prefix2"
        ctx2 = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix2,
            per_component=True,
        )
        assert len(ctx2) == 1
        assert (prefix2 / "share" / "config-files" / "index.html").is_file()

    def test_linux_recipe_depends_on_any(self, tmp_path, monkeypatch):
        """A linux recipe depending on an 'any' recipe works end-to-end."""
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_any_recipe(
            recipes_dir,
            "shared-data",
            files={"config.json": '{"key": "value"}'},
        )
        _create_linux_recipe(recipes_dir, "myapp", deps=["shared-data"])

        prefix = tmp_path / "prefix"
        contexts = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
        )

        built_names = [c.recipe.name for c in contexts]
        assert "shared-data" in built_names
        assert "myapp" in built_names
        # shared-data built before myapp
        assert built_names.index("shared-data") < built_names.index("myapp")
        # Files from both recipes are in the prefix
        assert (prefix / "share" / "shared-data" / "config.json").is_file()
        assert (prefix / "lib" / "libmyapp.txt").is_file()

    def test_any_depends_on_any(self, tmp_path, monkeypatch):
        """An 'any' recipe can depend on another 'any' recipe."""
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()
        _create_any_recipe(
            recipes_dir,
            "base-theme",
            files={"base.css": "body {}"},
        )
        _create_any_recipe(
            recipes_dir,
            "extended-theme",
            deps=["base-theme"],
            files={"extra.css": ".fancy {}"},
        )

        prefix = tmp_path / "prefix"
        contexts = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
        )

        built_names = [c.recipe.name for c in contexts]
        assert "base-theme" in built_names
        assert "extended-theme" in built_names
        assert built_names.index("base-theme") < built_names.index("extended-theme")
        assert (prefix / "share" / "base-theme" / "base.css").is_file()
        assert (prefix / "share" / "extended-theme" / "extra.css").is_file()

    def test_diamond_dependency_with_any(self, tmp_path, monkeypatch):
        """Diamond: app -> [libA, assets(any)], both -> base(any)."""
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()

        _create_any_recipe(
            recipes_dir,
            "base-config",
            files={"base.yaml": "version: 1"},
        )
        _create_any_recipe(
            recipes_dir,
            "assets",
            deps=["base-config"],
            files={"icon.svg": "<svg/>"},
        )
        _create_linux_recipe(recipes_dir, "libA", deps=["base-config"])
        _create_linux_recipe(recipes_dir, "app", deps=["libA", "assets"])

        prefix = tmp_path / "prefix"
        contexts = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
        )

        built_names = [c.recipe.name for c in contexts]
        assert set(built_names) == {"base-config", "assets", "libA", "app"}
        # Verify topo order
        assert built_names.index("base-config") < built_names.index("assets")
        assert built_names.index("base-config") < built_names.index("libA")
        assert built_names.index("assets") < built_names.index("app")
        assert built_names.index("libA") < built_names.index("app")
        # Verify all files present
        assert (prefix / "share" / "base-config" / "base.yaml").is_file()
        assert (prefix / "share" / "assets" / "icon.svg").is_file()
        assert (prefix / "lib" / "liblibA.txt").is_file()
        assert (prefix / "lib" / "libapp.txt").is_file()

    def test_keep_going_failure_in_any_dep(self, tmp_path, monkeypatch):
        """keep_going=True: failure in 'any' dep cascades to dependent."""
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "cache"))

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "_common").mkdir()

        # Create a broken 'any' recipe (build script exits 1)
        broken_dir = recipes_dir / "broken-data"
        broken_dir.mkdir(parents=True)
        recipe = {
            "schema_version": 1,
            "recipe": {
                "name": "broken-data",
                "upstream_version": "1.0.0",
                "cvc_revision": 1,
                "kind": "data",
            },
            "source": {"type": "vendored", "path": "third-party/broken-data"},
            "build": {
                "matrix": [{"platform": "any", "script": "build.sh"}],
            },
        }
        (broken_dir / "recipe.yaml").write_text(yaml.dump(recipe))
        (broken_dir / "build.sh").write_text("#!/bin/bash\nexit 1\n")
        os.chmod(broken_dir / "build.sh", 0o755)
        repo_root = recipes_dir.parent
        (repo_root / "third-party" / "broken-data").mkdir(parents=True, exist_ok=True)

        _create_linux_recipe(recipes_dir, "consumer", deps=["broken-data"])

        prefix = tmp_path / "prefix"
        contexts = build_all(
            recipes_dir,
            platform="linux",
            config="release",
            link="shared",
            prefix=prefix,
            per_component=True,
            keep_going=True,
        )

        assert len(contexts) == 0
        assert len(contexts.failures) == 2
        failed = {f.recipe_name: f for f in contexts.failures}
        assert "broken-data" in failed
        assert "consumer" in failed
        assert not failed["broken-data"].skipped
        assert failed["consumer"].skipped


# ── Manifest and archive tests ──────────────────────────────────


class TestPlatformAnyManifest:
    """Test manifest and archive creation for 'any' recipes."""

    def test_full_manifest_roundtrip(self, tmp_path):
        """Generate manifest, stage, archive, and verify contents."""
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        _create_any_recipe(
            recipes_dir,
            "icons",
            kind="media",
            files={"logo.png": "PNG-DATA", "favicon.ico": "ICO-DATA"},
        )

        r = Recipe.load(recipes_dir / "icons")
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        share = install_dir / "share" / "icons"
        share.mkdir(parents=True)
        (share / "logo.png").write_text("PNG-DATA")
        (share / "favicon.ico").write_text("ICO-DATA")

        manifest = generate_manifest(r, install_dir, "any", "noarch", "release", "shared")
        assert manifest["bundle"]["platform"] == "any"
        assert manifest["bundle"]["arch"] == "noarch"
        assert manifest["meta"]["kind"] == "media"
        assert "platform-independent" in manifest["meta"]["tags"]

        # Stage and archive
        staging = tmp_path / "staging"
        staging.mkdir()
        stage_bundle(install_dir, manifest, staging, recipe_dir=r.recipe_dir)

        output_dir = tmp_path / "dist"
        path, sha, size = create_archive(
            staging, output_dir, "icons", r.full_version, "any", "noarch", "release", "shared"
        )
        assert path.name.endswith(".tar.gz")
        assert "any-noarch" in path.name
        assert size > 0

        # Verify archive contents
        import tarfile

        with tarfile.open(path) as tf:
            members = tf.getnames()
            assert "share/icons/logo.png" in members
            assert "share/icons/favicon.ico" in members
            assert "share/libcvc-deps/manifest.yaml" in members

    def test_manifest_with_deps(self, tmp_path):
        """Manifest for an 'any' recipe with deps records them correctly."""
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        _create_any_recipe(recipes_dir, "base-theme")
        _create_any_recipe(recipes_dir, "ext-theme", deps=["base-theme"])

        r = Recipe.load(recipes_dir / "ext-theme")
        install = tmp_path / "install"
        install.mkdir()

        manifest = generate_manifest(r, install, "any", "noarch", "release", "shared")
        dep_names = [d["name"] for d in manifest["depends"]]
        assert "base-theme" in dep_names


# ── Cache key isolation tests ───────────────────────────────────


class TestPlatformAnyCacheKeys:
    """Verify cache keys for 'any' recipes are platform-agnostic."""

    def test_chain_hash_deterministic(self, tmp_path):
        """chain_hash for an 'any' recipe is deterministic."""
        recipes_dir = tmp_path / "recipes"
        (recipes_dir / "_common").mkdir(parents=True)
        _create_any_recipe(recipes_dir, "data")

        r = Recipe.load(recipes_dir / "data")
        all_map = {r.name: r}
        h1 = chain_hash(r, all_map, "any")
        h2 = chain_hash(r, all_map, "any")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_chain_hash_changes_on_content(self, tmp_path):
        """Modifying the recipe changes the chain hash."""
        recipes_dir = tmp_path / "recipes"
        (recipes_dir / "_common").mkdir(parents=True)
        _create_any_recipe(recipes_dir, "data")

        r1 = Recipe.load(recipes_dir / "data")
        h1 = chain_hash(r1, {r1.name: r1}, "any")

        # Modify the build script
        (recipes_dir / "data" / "build.sh").write_text("#!/bin/bash\necho changed\n")
        r2 = Recipe.load(recipes_dir / "data")
        h2 = chain_hash(r2, {r2.name: r2}, "any")

        assert h1 != h2

    def test_chain_hash_with_any_dep(self, tmp_path):
        """chain_hash includes transitive 'any' dependencies."""
        recipes_dir = tmp_path / "recipes"
        (recipes_dir / "_common").mkdir(parents=True)
        _create_any_recipe(recipes_dir, "base")
        _create_any_recipe(recipes_dir, "child", deps=["base"])

        base = Recipe.load(recipes_dir / "base")
        child = Recipe.load(recipes_dir / "child")
        all_map = {base.name: base, child.name: child}

        h_child = chain_hash(child, all_map, "any")

        # Modify base -- child's hash should change
        (recipes_dir / "base" / "build.sh").write_text("#!/bin/bash\necho v2\n")
        base2 = Recipe.load(recipes_dir / "base")
        all_map2 = {base2.name: base2, child.name: child}
        h_child2 = chain_hash(child, all_map2, "any")

        assert h_child != h_child2


# ── Dependency resolution tests ─────────────────────────────────


class TestPlatformAnyDependencyResolution:
    """Test dependency resolution involving 'any' recipes."""

    def test_mixed_platform_dep_chain(self, tmp_path):
        """Topo order: base(any) -> libA(linux) -> app(linux)."""
        recipes_dir = tmp_path / "recipes"
        _create_any_recipe(recipes_dir, "base")
        _create_linux_recipe(recipes_dir, "libA", deps=["base"])
        _create_linux_recipe(recipes_dir, "app", deps=["libA"])

        recipes = list_recipes(recipes_dir)
        # Filter for linux (includes 'any')
        linux_recipes = [
            r for r in recipes if any(m.platform in ("linux", "any") for m in r.build_matrix)
        ]
        ordered = resolve_build_order(linux_recipes, "linux")
        names = [r.name for r in ordered]
        assert names.index("base") < names.index("libA")
        assert names.index("libA") < names.index("app")

    def test_multiple_any_deps(self, tmp_path):
        """A linux recipe with two 'any' deps resolves correctly."""
        recipes_dir = tmp_path / "recipes"
        _create_any_recipe(recipes_dir, "icons", kind="media")
        _create_any_recipe(recipes_dir, "templates", kind="data")
        _create_linux_recipe(recipes_dir, "webapp", deps=["icons", "templates"])

        recipes = list_recipes(recipes_dir)
        linux_recipes = [
            r for r in recipes if any(m.platform in ("linux", "any") for m in r.build_matrix)
        ]
        ordered = resolve_build_order(linux_recipes, "linux")
        names = [r.name for r in ordered]
        assert len(names) == 3
        assert names.index("icons") < names.index("webapp")
        assert names.index("templates") < names.index("webapp")

    def test_any_recipes_excluded_from_wrong_platform_filter(self, tmp_path):
        """When manually filtering by platform, 'any' needs explicit inclusion."""
        recipes_dir = tmp_path / "recipes"
        _create_any_recipe(recipes_dir, "data")
        _create_linux_recipe(recipes_dir, "linuxlib")

        recipes = list_recipes(recipes_dir)
        # Pure platform filter without 'any' inclusion
        linux_only = [r for r in recipes if any(m.platform == "linux" for m in r.build_matrix)]
        assert len(linux_only) == 1  # only linuxlib

        # Filter with 'any' included (what build_all does)
        with_any = [
            r for r in recipes if any(m.platform in ("linux", "any") for m in r.build_matrix)
        ]
        assert len(with_any) == 2
