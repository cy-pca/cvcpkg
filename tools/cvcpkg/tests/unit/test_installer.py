"""Tests for cvcpkg.installer — download, verify, extract bundles."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from cvcpkg.errors import InstallError
from cvcpkg.installer import _archive_filename, extract_bundle
from cvcpkg.manifest import CatalogEntry

# ── extract_bundle ──────────────────────────────────────────────


class TestExtractBundle:
    def _make_tar_gz(self, tmp_path, name="archive.tar.gz", members=None):
        """Create a test .tar.gz archive."""
        archive_path = tmp_path / name
        with tarfile.open(archive_path, "w:gz") as tf:
            for fname, content in members or [("lib/libz.so", b"fake lib")]:
                info = tarfile.TarInfo(name=fname)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        return archive_path

    def _make_zip(self, tmp_path, name="archive.zip", members=None):
        archive_path = tmp_path / name
        with zipfile.ZipFile(archive_path, "w") as zf:
            for fname, content in members or [("lib/libz.so", b"fake lib")]:
                zf.writestr(fname, content)
        return archive_path

    def test_extract_tar_gz(self, tmp_path):
        archive = self._make_tar_gz(tmp_path)
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "libz.so").exists()

    def test_extract_zip(self, tmp_path):
        archive = self._make_zip(tmp_path)
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "libz.so").exists()

    def test_creates_prefix(self, tmp_path):
        archive = self._make_tar_gz(tmp_path)
        prefix = tmp_path / "new" / "deep" / "prefix"
        extract_bundle(archive, prefix)
        assert prefix.is_dir()

    def test_path_traversal_tar_absolute(self, tmp_path):
        archive_path = tmp_path / "evil.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            info = tarfile.TarInfo(name="/etc/passwd")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"evil!"))
        prefix = tmp_path / "prefix"
        with pytest.raises(InstallError, match="unsafe path"):
            extract_bundle(archive_path, prefix)

    def test_path_traversal_tar_dotdot(self, tmp_path):
        archive_path = tmp_path / "evil.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"evil!"))
        prefix = tmp_path / "prefix"
        with pytest.raises(InstallError, match="unsafe path"):
            extract_bundle(archive_path, prefix)

    def test_path_traversal_zip_absolute(self, tmp_path):
        archive = self._make_zip(tmp_path, members=[("/etc/passwd", b"nope")])
        prefix = tmp_path / "prefix"
        with pytest.raises(InstallError, match="unsafe path"):
            extract_bundle(archive, prefix)

    def test_path_traversal_zip_dotdot(self, tmp_path):
        archive = self._make_zip(tmp_path, members=[("../../etc/passwd", b"nope")])
        prefix = tmp_path / "prefix"
        with pytest.raises(InstallError, match="unsafe path"):
            extract_bundle(archive, prefix)

    def test_unsupported_format(self, tmp_path):
        archive = tmp_path / "archive.rar"
        archive.write_bytes(b"fake rar")
        with pytest.raises(InstallError, match="unsupported archive format"):
            extract_bundle(archive, tmp_path / "prefix")

    def test_multiple_files(self, tmp_path):
        members = [
            ("lib/liba.so", b"lib a"),
            ("lib/libb.so", b"lib b"),
            ("include/a.h", b"header a"),
        ]
        archive = self._make_tar_gz(tmp_path, members=members)
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "liba.so").exists()
        assert (prefix / "lib" / "libb.so").exists()
        assert (prefix / "include" / "a.h").exists()

    def test_extract_tar_bz2(self, tmp_path):
        archive = tmp_path / "archive.tar.bz2"
        with tarfile.open(archive, "w:bz2") as tf:
            info = tarfile.TarInfo(name="lib/libz.so")
            info.size = 8
            tf.addfile(info, io.BytesIO(b"fake lib"))
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "libz.so").exists()

    def test_extract_tar_xz(self, tmp_path):
        archive = tmp_path / "archive.tar.xz"
        with tarfile.open(archive, "w:xz") as tf:
            info = tarfile.TarInfo(name="lib/libz.so")
            info.size = 8
            tf.addfile(info, io.BytesIO(b"fake lib"))
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "libz.so").exists()

    def test_extract_plain_tar(self, tmp_path):
        archive = tmp_path / "archive.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="lib/libz.so")
            info.size = 8
            tf.addfile(info, io.BytesIO(b"fake lib"))
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "libz.so").exists()

    def test_case_insensitive_suffix(self, tmp_path):
        """Archive names with mixed case should still be recognized."""
        archive = self._make_tar_gz(tmp_path, name="Archive.TAR.GZ")
        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        assert (prefix / "lib" / "libz.so").exists()


# ── _archive_filename ──────────────────────────────────────────


class TestArchiveFilename:
    def test_from_url(self):
        e = CatalogEntry(
            name="zlib",
            version="1.3.1+cvc.1",
            upstream_version="1.3.1",
            cvc_revision=1,
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="",
            size_bytes=0,
            archive_url="https://example.com/zlib-1.3.1.tar.gz",
            source_release="",
        )
        assert _archive_filename(e) == "zlib-1.3.1.tar.gz"

    def test_fallback_linux(self):
        e = CatalogEntry(
            name="zlib",
            version="1.3.1+cvc.1",
            upstream_version="1.3.1",
            cvc_revision=1,
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="",
            size_bytes=0,
            archive_url="",
            source_release="",
        )
        fn = _archive_filename(e)
        assert fn.endswith(".tar.gz")
        assert "zlib" in fn

    def test_fallback_windows(self):
        e = CatalogEntry(
            name="zlib",
            version="1.3.1+cvc.1",
            upstream_version="1.3.1",
            cvc_revision=1,
            platform="windows",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="",
            size_bytes=0,
            archive_url="",
            source_release="",
        )
        fn = _archive_filename(e)
        assert fn.endswith(".zip")
