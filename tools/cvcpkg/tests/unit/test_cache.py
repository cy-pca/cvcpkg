"""Tests for cvcpkg.cache — content-addressed download cache."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cvcpkg.cache import (
    cache_path,
    default_cache_dir,
    file_sha256,
    gc,
    is_cached,
    store,
)


class TestDefaultCacheDir:
    def test_cvcpkg_cache_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CVCPKG_CACHE", str(tmp_path / "custom"))
        assert default_cache_dir() == tmp_path / "custom"

    def test_xdg_cache_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CVCPKG_CACHE", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert default_cache_dir() == tmp_path / "xdg" / "cvcpkg"

    def test_fallback_home(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CACHE", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        result = default_cache_dir()
        assert result == Path.home() / ".cache" / "cvcpkg"


class TestCachePath:
    def test_construction(self, tmp_path):
        sha = "a" * 64
        p = cache_path(tmp_path, sha, "zlib-1.3.1.tar.gz")
        assert p == tmp_path / ("a" * 64) / "zlib-1.3.1.tar.gz"


class TestFileSha256:
    def test_correct_hash(self, tmp_path):
        content = b"hello, cvcpkg!"
        p = tmp_path / "test.bin"
        p.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert file_sha256(p) == expected

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty"
        p.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert file_sha256(p) == expected


class TestStore:
    def test_stores_and_returns_path(self, tmp_path):
        data = b"some archive content"
        sha = hashlib.sha256(data).hexdigest()
        p = store(tmp_path, sha, "pkg.tar.gz", data)
        assert p.exists()
        assert p.read_bytes() == data
        assert p.parent.name == sha

    def test_creates_subdirectory(self, tmp_path):
        sha = "b" * 64
        store(tmp_path, sha, "file.zip", b"data")
        assert (tmp_path / sha).is_dir()


class TestIsCached:
    def test_not_cached(self, tmp_path):
        assert is_cached(tmp_path, "a" * 64, "pkg.tar.gz") is False

    def test_cached_valid(self, tmp_path):
        data = b"valid archive data"
        sha = hashlib.sha256(data).hexdigest()
        store(tmp_path, sha, "pkg.tar.gz", data)
        assert is_cached(tmp_path, sha, "pkg.tar.gz") is True

    def test_cached_corrupt(self, tmp_path):
        data = b"original"
        sha = hashlib.sha256(data).hexdigest()
        p = store(tmp_path, sha, "pkg.tar.gz", data)
        # Corrupt the file
        p.write_bytes(b"corrupted")
        assert is_cached(tmp_path, sha, "pkg.tar.gz") is False


class TestGC:
    def test_gc_removes_unreferenced(self, tmp_path):
        sha1 = "a" * 64
        sha2 = "b" * 64
        store(tmp_path, sha1, "keep.tar.gz", b"keep")
        store(tmp_path, sha2, "remove.tar.gz", b"remove")

        removed = gc(tmp_path, {sha1})
        assert removed == 1
        assert (tmp_path / sha1).exists()
        assert not (tmp_path / sha2).exists()

    def test_gc_empty_cache(self, tmp_path):
        assert gc(tmp_path, set()) == 0

    def test_gc_nonexistent_dir(self, tmp_path):
        assert gc(tmp_path / "nonexistent", set()) == 0

    def test_gc_keeps_all_referenced(self, tmp_path):
        sha = "c" * 64
        store(tmp_path, sha, "file.tar.gz", b"data")
        removed = gc(tmp_path, {sha})
        assert removed == 0
        assert (tmp_path / sha).exists()

    def test_gc_removes_all_unreferenced(self, tmp_path):
        for i in range(3):
            store(tmp_path, f"{i}" * 64, f"file{i}.tar.gz", b"data")
        removed = gc(tmp_path, set())
        assert removed == 3
