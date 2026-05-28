"""Tests for cvcpkg.build_cache — content-addressed build cache."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
import yaml

from cvcpkg.build_cache import (
    BuildCache,
    CacheEntryMeta,
    cache_key,
    default_build_cache_dir,
)

# ── Helpers ─────────────────────────────────────────────────────


def _populate_install_dir(install_dir: Path) -> None:
    """Create a minimal install directory with some files."""
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.so").write_text("fake lib")
    (install_dir / "include").mkdir(parents=True)
    (install_dir / "include" / "foo.h").write_text("#pragma once")


def _fake_chain_hash() -> str:
    return hashlib.sha256(b"test-recipe-content").hexdigest()


# ── cache_key ───────────────────────────────────────────────────


class TestCacheKey:
    def test_format(self):
        k = cache_key("abc123", "linux", "x86_64", "release", "shared")
        assert k == "abc123-linux-x86_64-release-shared"

    def test_different_platform(self):
        k1 = cache_key("abc", "linux", "x86_64", "release", "shared")
        k2 = cache_key("abc", "macos", "x86_64", "release", "shared")
        assert k1 != k2

    def test_different_arch(self):
        k1 = cache_key("abc", "linux", "x86_64", "release", "shared")
        k2 = cache_key("abc", "linux", "arm64", "release", "shared")
        assert k1 != k2

    def test_different_config(self):
        k1 = cache_key("abc", "linux", "x86_64", "release", "shared")
        k2 = cache_key("abc", "linux", "x86_64", "debug", "shared")
        assert k1 != k2

    def test_different_link(self):
        k1 = cache_key("abc", "linux", "x86_64", "release", "shared")
        k2 = cache_key("abc", "linux", "x86_64", "release", "static")
        assert k1 != k2


# ── default_build_cache_dir ─────────────────────────────────────


class TestDefaultBuildCacheDir:
    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CVCPKG_BUILD_CACHE", str(tmp_path / "custom"))
        assert default_build_cache_dir() == tmp_path / "custom"

    def test_xdg_cache_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CVCPKG_BUILD_CACHE", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert default_build_cache_dir() == tmp_path / "xdg" / "cvcpkg" / "builds"

    def test_fallback_home(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_BUILD_CACHE", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        result = default_build_cache_dir()
        assert result == Path.home() / ".cache" / "cvcpkg" / "builds"


# ── CacheEntryMeta ──────────────────────────────────────────────


class TestCacheEntryMeta:
    def test_roundtrip(self):
        meta = CacheEntryMeta(
            name="zlib",
            version="1.3.1+cvc.1",
            chain_hash="a" * 64,
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
            archive_sha256="b" * 64,
            archive_size_bytes=12345,
            stored_at="2026-01-01T00:00:00+00:00",
            last_used_at="2026-01-02T00:00:00+00:00",
            org="myorg",
        )
        text = meta.to_json()
        restored = CacheEntryMeta.from_json(text)
        assert restored.name == "zlib"
        assert restored.org == "myorg"
        assert restored.archive_size_bytes == 12345

    def test_extra_keys_ignored(self):
        data = {
            "name": "pkg",
            "version": "1.0",
            "chain_hash": "x" * 64,
            "platform": "linux",
            "arch": "x86_64",
            "config": "release",
            "link": "shared",
            "archive_sha256": "y" * 64,
            "archive_size_bytes": 100,
            "future_field": "should be ignored",
        }
        meta = CacheEntryMeta.from_json(json.dumps(data))
        assert meta.name == "pkg"


# ── BuildCache.lookup ───────────────────────────────────────────


class TestBuildCacheLookup:
    def test_miss_empty_cache(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        assert bc.lookup("a" * 64, "linux", "x86_64", "release", "shared") is None

    def test_hit_after_store(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        result = bc.lookup(ch, "linux", "x86_64", "release", "shared")
        assert result is not None
        assert result.is_file()
        assert result.name == "install.tar.gz"

    def test_miss_wrong_platform(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        assert bc.lookup(ch, "macos", "x86_64", "release", "shared") is None

    def test_miss_wrong_config(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        assert bc.lookup(ch, "linux", "x86_64", "debug", "shared") is None

    def test_miss_wrong_link(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        assert bc.lookup(ch, "linux", "x86_64", "release", "static") is None

    def test_corrupted_archive_evicted(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        archive = bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        # Corrupt the archive.
        archive.write_bytes(b"corrupted data")
        assert bc.lookup(ch, "linux", "x86_64", "release", "shared") is None
        # Entry dir should be removed.
        assert not archive.parent.is_dir()

    def test_updates_last_used_at(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        meta_before = bc.info(ch, "linux", "x86_64", "release", "shared")
        time.sleep(0.05)
        bc.lookup(ch, "linux", "x86_64", "release", "shared")
        meta_after = bc.info(ch, "linux", "x86_64", "release", "shared")

        assert meta_after is not None
        assert meta_before is not None
        assert meta_after.last_used_at >= meta_before.last_used_at


# ── BuildCache.store ────────────────────────────────────────────


class TestBuildCacheStore:
    def test_creates_entry(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        archive = bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")
        assert archive.is_file()
        assert (archive.parent / "meta.json").is_file()

    def test_meta_content(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "testpkg", "2.0+cvc.1", ch, "linux", "x86_64", "release", "shared")
        meta = bc.info(ch, "linux", "x86_64", "release", "shared")
        assert meta is not None
        assert meta.name == "testpkg"
        assert meta.version == "2.0+cvc.1"
        assert meta.chain_hash == ch
        assert meta.platform == "linux"
        assert meta.archive_size_bytes > 0
        assert meta.stored_at != ""

    def test_store_with_org(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(
            install,
            "pkg",
            "1.0",
            ch,
            "linux",
            "x86_64",
            "release",
            "shared",
            org="myorg",
        )
        meta = bc.info(ch, "linux", "x86_64", "release", "shared")
        assert meta is not None
        assert meta.org == "myorg"


# ── BuildCache.restore ──────────────────────────────────────────


class TestBuildCacheRestore:
    def test_restore_contents(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        archive = bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        target = tmp_path / "restored"
        bc.restore(archive, target)
        assert (target / "lib" / "libfoo.so").is_file()
        assert (target / "include" / "foo.h").is_file()
        assert (target / "lib" / "libfoo.so").read_text() == "fake lib"


# ── BuildCache.evict ────────────────────────────────────────────


class TestBuildCacheEvict:
    def test_evict_existing(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        ch = _fake_chain_hash()
        bc.store(install, "pkg", "1.0", ch, "linux", "x86_64", "release", "shared")

        assert bc.evict(ch, "linux", "x86_64", "release", "shared") is True
        assert bc.lookup(ch, "linux", "x86_64", "release", "shared") is None

    def test_evict_nonexistent(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        assert bc.evict("x" * 64, "linux", "x86_64", "release", "shared") is False


# ── BuildCache.list_entries ─────────────────────────────────────


class TestBuildCacheList:
    def test_empty(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        assert bc.list_entries() == []

    def test_lists_stored_entries(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        bc.store(
            install,
            "pkg1",
            "1.0",
            hashlib.sha256(b"1").hexdigest(),
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        bc.store(
            install,
            "pkg2",
            "2.0",
            hashlib.sha256(b"2").hexdigest(),
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        entries = bc.list_entries()
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert names == {"pkg1", "pkg2"}

    def test_nonexistent_dir(self, tmp_path):
        bc = BuildCache(tmp_path / "nonexistent")
        assert bc.list_entries() == []


# ── BuildCache.purge ────────────────────────────────────────────


class TestBuildCachePurge:
    def test_purge_all(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        bc.store(
            install,
            "pkg",
            "1.0",
            _fake_chain_hash(),
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        removed = bc.purge(max_size_bytes=0)
        assert removed == 1
        assert bc.list_entries() == []

    def test_purge_by_max_size(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        # Store 3 entries.
        for i in range(3):
            ch = hashlib.sha256(str(i).encode()).hexdigest()
            bc.store(install, f"pkg{i}", "1.0", ch, "linux", "x86_64", "release", "shared")
            time.sleep(0.05)  # ensure different timestamps

        total = bc.total_size_bytes()
        # Leave room for roughly 2 entries.
        limit = int(total * 0.8)
        removed = bc.purge(max_size_bytes=limit)
        assert removed >= 1
        assert bc.total_size_bytes() <= limit

    def test_purge_by_age(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        bc.store(
            install,
            "old",
            "1.0",
            _fake_chain_hash(),
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        # Backdate the entry.
        key = cache_key(_fake_chain_hash(), "linux", "x86_64", "release", "shared")
        meta_path = bc.cache_dir / key / "meta.json"
        meta = CacheEntryMeta.from_json(meta_path.read_text())
        meta.last_used_at = "2020-01-01T00:00:00+00:00"
        meta.stored_at = "2020-01-01T00:00:00+00:00"
        meta_path.write_text(meta.to_json())

        removed = bc.purge(max_age_seconds=1)
        assert removed == 1

    def test_purge_empty_cache(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        assert bc.purge(max_size_bytes=0) == 0


# ── BuildCache.total_size_bytes ─────────────────────────────────


class TestBuildCacheTotalSize:
    def test_empty(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        assert bc.total_size_bytes() == 0

    def test_nonzero_after_store(self, tmp_path):
        bc = BuildCache(tmp_path / "cache")
        install = tmp_path / "install"
        _populate_install_dir(install)
        bc.store(
            install,
            "pkg",
            "1.0",
            _fake_chain_hash(),
            "linux",
            "x86_64",
            "release",
            "shared",
        )
        assert bc.total_size_bytes() > 0


# ── chain_hash with _common ────────────────────────────────────


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


def _write_recipe(recipe_dir: Path, recipe_dict: dict) -> Path:
    recipe_dir.mkdir(parents=True, exist_ok=True)
    p = recipe_dir / "recipe.yaml"
    p.write_text(yaml.dump(recipe_dict, default_flow_style=False))
    return p


class TestChainHashCommon:
    def test_includes_common_scripts(self, tmp_path):
        """Modifying a _common/ script should change the chain_hash."""
        from cvcpkg.builder import Recipe, chain_hash

        recipes_dir = tmp_path / "recipes"
        common = recipes_dir / "_common"
        common.mkdir(parents=True)
        (common / "env-linux.sh").write_text("export FOO=1")

        recipe_dir = recipes_dir / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        (recipe_dir / "build.sh").write_text("#!/bin/bash\nmake install")
        recipe = Recipe.load(recipe_dir)

        hash1 = chain_hash(recipe, {recipe.name: recipe}, "linux")

        # Modify _common/ script.
        (common / "env-linux.sh").write_text("export FOO=2")
        hash2 = chain_hash(recipe, {recipe.name: recipe}, "linux")

        assert hash1 != hash2

    def test_stable_hash(self, tmp_path):
        """Same inputs produce the same hash."""
        from cvcpkg.builder import Recipe, chain_hash

        recipes_dir = tmp_path / "recipes"
        common = recipes_dir / "_common"
        common.mkdir(parents=True)
        (common / "env-linux.sh").write_text("export FOO=1")

        recipe_dir = recipes_dir / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        (recipe_dir / "build.sh").write_text("#!/bin/bash\nmake install")
        recipe = Recipe.load(recipe_dir)

        hash1 = chain_hash(recipe, {recipe.name: recipe}, "linux")
        hash2 = chain_hash(recipe, {recipe.name: recipe}, "linux")
        assert hash1 == hash2

    def test_no_common_dir(self, tmp_path):
        """chain_hash works when _common/ does not exist."""
        from cvcpkg.builder import Recipe, chain_hash

        recipes_dir = tmp_path / "recipes"
        recipe_dir = recipes_dir / "testpkg"
        _write_recipe(recipe_dir, MINIMAL_RECIPE)
        (recipe_dir / "build.sh").write_text("#!/bin/bash\nmake install")
        recipe = Recipe.load(recipe_dir)

        h = chain_hash(recipe, {recipe.name: recipe}, "linux")
        assert len(h) == 64  # SHA-256 hex digest

    def test_chain_hash_cascade(self, tmp_path):
        """Changing a dependency recipe changes downstream hashes."""
        from cvcpkg.builder import Recipe, chain_hash

        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir(parents=True)

        # Create dependency recipe.
        dep_dict = dict(MINIMAL_RECIPE)
        dep_dict = {
            **MINIMAL_RECIPE,
            "recipe": {
                "name": "deplib",
                "upstream_version": "1.0.0",
                "cvc_revision": 1,
            },
        }
        dep_dir = recipes_dir / "deplib"
        _write_recipe(dep_dir, dep_dict)
        (dep_dir / "build.sh").write_text("#!/bin/bash\nmake install")

        # Create main recipe depending on deplib.
        main_dict = {
            **MINIMAL_RECIPE,
            "recipe": {
                "name": "mainpkg",
                "upstream_version": "2.0.0",
                "cvc_revision": 1,
            },
            "depends": {"build": ["deplib"]},
        }
        main_dir = recipes_dir / "mainpkg"
        _write_recipe(main_dir, main_dict)
        (main_dir / "build.sh").write_text("#!/bin/bash\nmake install")

        dep = Recipe.load(dep_dir)
        main = Recipe.load(main_dir)
        all_recipes = {dep.name: dep, main.name: main}

        hash_before = chain_hash(main, all_recipes, "linux")

        # Modify the dependency recipe.
        dep_dict["recipe"]["cvc_revision"] = 2
        _write_recipe(dep_dir, dep_dict)
        dep2 = Recipe.load(dep_dir)
        all_recipes2 = {dep2.name: dep2, main.name: main}

        hash_after = chain_hash(main, all_recipes2, "linux")
        assert hash_before != hash_after


# ── _common_scripts_hash ───────────────────────────────────────


class TestCommonScriptsHash:
    def test_returns_empty_for_missing_dir(self, tmp_path):
        from cvcpkg.builder import _common_scripts_hash

        assert _common_scripts_hash(tmp_path) == ""

    def test_returns_hash_for_existing(self, tmp_path):
        from cvcpkg.builder import _common_scripts_hash

        common = tmp_path / "_common"
        common.mkdir()
        (common / "env-linux.sh").write_text("export FOO=1")
        h = _common_scripts_hash(tmp_path)
        assert len(h) == 64

    def test_deterministic(self, tmp_path):
        from cvcpkg.builder import _common_scripts_hash

        common = tmp_path / "_common"
        common.mkdir()
        (common / "a.sh").write_text("echo a")
        (common / "b.sh").write_text("echo b")
        h1 = _common_scripts_hash(tmp_path)
        h2 = _common_scripts_hash(tmp_path)
        assert h1 == h2
