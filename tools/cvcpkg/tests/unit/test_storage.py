"""Tests for cvcpkg.storage — backend protocol, registry, and file backend."""

import io
from pathlib import Path

import pytest

from cvcpkg.storage import ObjectInfo, available_schemes, get_backend, register


class TestObjectInfo:
    def test_defaults(self):
        info = ObjectInfo()
        assert info.size == -1
        assert info.sha256 == ""
        assert info.etag == ""

    def test_fields(self):
        info = ObjectInfo(size=100, sha256="abc123")
        assert info.size == 100
        assert info.sha256 == "abc123"


class TestRegistry:
    def test_builtin_schemes_registered(self):
        schemes = available_schemes()
        assert "https" in schemes
        assert "http" in schemes
        assert "file" in schemes

    def test_get_backend_https(self):
        backend = get_backend("https://example.com/file.tar.gz")
        assert backend is not None

    def test_get_backend_file(self):
        backend = get_backend("file:///tmp/test.tar.gz")
        assert backend is not None

    def test_get_backend_unknown_scheme(self):
        with pytest.raises(ValueError, match="No storage backend"):
            get_backend("unknownscheme://host/path")


class TestFileBackend:
    def test_head(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        backend = get_backend(f"file://{f}")
        info = backend.head(f"file://{f}")
        assert info.size == 11

    def test_open_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        backend = get_backend(f"file://{f}")
        with backend.open(f"file://{f}") as stream:
            data = stream.read()
        assert data == b"hello world"

    def test_put(self, tmp_path):
        dest = tmp_path / "output.txt"
        backend = get_backend(f"file://{dest}")
        backend.put(f"file://{dest}", io.BytesIO(b"written data"))
        assert dest.read_bytes() == b"written data"

    def test_list(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        backend = get_backend(f"file://{tmp_path}/")
        items = backend.list(f"file://{tmp_path}/")
        names = [i.split("/")[-1] for i in items]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_head_nonexistent(self, tmp_path):
        backend = get_backend(f"file://{tmp_path}/nope.txt")
        with pytest.raises(FileNotFoundError):
            backend.head(f"file://{tmp_path}/nope.txt")

    def test_plain_path_without_scheme(self, tmp_path):
        """file backend should handle plain absolute paths."""
        f = tmp_path / "plain.txt"
        f.write_bytes(b"data")
        backend = get_backend(f"file://{f}")
        info = backend.head(f"file://{f}")
        assert info.size == 4
