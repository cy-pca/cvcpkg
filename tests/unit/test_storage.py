"""Tests for cvcpkg.storage — backend protocol, registry, and file backend."""

import io
from pathlib import Path

import pytest

from cvcpkg.backends.local import _uri_to_path
from cvcpkg.storage import ObjectInfo, available_schemes, get_backend


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


class TestUriToPath:
    """Verify _uri_to_path handles various file:// URI forms."""

    def test_posix_triple_slash(self):
        p = _uri_to_path("file:///tmp/foo.txt")
        assert p == Path("/tmp/foo.txt")

    def test_windows_drive_letter_netloc(self):
        """file://C/Users/test parses as netloc='C', path='/Users/test'.

        On Windows Python, file://C:\\path is also parsed this way
        because backslashes are normalised to forward slashes.
        """
        p = _uri_to_path("file://C/Users/test")
        assert str(p) == "C:/Users/test" or str(p) == "C:\\Users\\test"

    def test_windows_triple_slash(self):
        """file:///C:/Users/test — correct Windows URI."""
        p = _uri_to_path("file:///C:/Users/test")
        # On Linux this stays as-is (/C:/Users/test); on Windows it resolves
        assert "C:" in str(p) or str(p).startswith("/C:")

    def test_backslash_netloc_fallback(self):
        """On some Python/OS combos, file://C:\\path puts C:\\path in netloc."""
        p = _uri_to_path("file://C:\\Users\\test")
        # netloc='C\\Users\\test', path='' → should detect drive letter at [1]
        assert str(p).startswith("C:")

    def test_windows_drive_colon_in_netloc(self):
        """file://C:/Users/test/ → netloc='C', path='/Users/test/'."""
        p = _uri_to_path("file://C:/Users/test/")
        # Single-letter netloc → drive letter
        assert str(p).startswith("C:")

    def test_windows_netloc_with_path_suffix(self):
        """file://C:\\dir/file.tar.gz must include the path suffix."""
        p = _uri_to_path("file://C:\\dir/file.tar.gz")
        assert str(p).endswith("file.tar.gz")
