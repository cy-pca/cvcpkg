"""Unit tests for cvcpkg.server.archive_store — the backend-aware primitives
that publish/download/migration/doctor all share.

Covers URI/path composition, the persisted active-storage config, and the
read/write/exist/size/checksum/copy primitives against both a local
(``file://``) backend and a remote-style in-memory backend.
"""

from __future__ import annotations

import hashlib
import io

import pytest

from cvcpkg import storage
from cvcpkg.server import archive_store

# ── in-memory "remote" backend ──────────────────────────────────


class MemoryBackend:
    schemes = ("mem",)

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def head(self, uri: str):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return storage.ObjectInfo(size=len(self.objects[uri]))

    def open(self, uri: str):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return io.BytesIO(self.objects[uri])

    def supports_range(self, uri: str) -> bool:
        return False

    def put(self, uri: str, data, size: int = -1) -> None:
        self.objects[uri] = data.read()

    def list(self, uri: str):
        for k in list(self.objects):
            if k.startswith(uri):
                yield k[len(uri) :]


@pytest.fixture
def mem():
    be = MemoryBackend()
    storage.register(be)
    try:
        yield be
    finally:
        storage._registry.pop("mem", None)


# ── URI / path composition ──────────────────────────────────────


def test_scheme_and_is_local():
    assert archive_store.scheme_of("file:///x") == "file"
    assert archive_store.scheme_of("s3://b/k") == "s3"
    assert archive_store.scheme_of("/plain/path") == "file"  # no scheme → file
    assert archive_store.is_local("file:///x")
    assert not archive_store.is_local("s3://b/k")


def test_archive_uri_composition():
    assert archive_store.archive_uri("s3://bucket/prefix", "a.tar.zst") == (
        "s3://bucket/prefix/archives/a.tar.zst"
    )
    # A trailing slash on the storage root is normalised away.
    assert archive_store.archive_uri("s3://bucket/prefix/", "a.tar.zst") == (
        "s3://bucket/prefix/archives/a.tar.zst"
    )


def test_local_root_and_path(tmp_path):
    uri = f"file://{tmp_path}"
    assert archive_store.local_root(uri) == tmp_path / "archives"
    assert archive_store.local_path(uri, "a.bin") == tmp_path / "archives" / "a.bin"
    # Remote backends have no local path.
    assert archive_store.local_root("s3://b/k") is None
    assert archive_store.local_path("s3://b/k", "a.bin") is None


# ── persisted active-storage config ─────────────────────────────


def test_storage_uri_persistence_roundtrip(tmp_path):
    default = f"file://{tmp_path}"
    # No config yet → default.
    assert archive_store.load_storage_uri(tmp_path, default) == default
    archive_store.save_storage_uri(tmp_path, "s3://bucket/prefix")
    # Now the persisted value wins.
    assert archive_store.load_storage_uri(tmp_path, default) == "s3://bucket/prefix"


# ── local backend primitives ────────────────────────────────────


def test_store_open_exists_size_local(tmp_path):
    uri = f"file://{tmp_path}"
    data = b"hello-archive" * 1000
    staging = tmp_path / "staging.upload"
    staging.write_bytes(data)

    archive_store.store(uri, "pkg.tar.zst", staging)

    assert not staging.exists()  # moved, not copied
    assert archive_store.exists(uri, "pkg.tar.zst")
    assert archive_store.size(uri, "pkg.tar.zst") == len(data)
    with archive_store.open_stream(uri, "pkg.tar.zst") as fh:
        assert fh.read() == data
    assert archive_store.sha256_of(uri, "pkg.tar.zst") == hashlib.sha256(data).hexdigest()


def test_exists_false_for_absent(tmp_path):
    assert not archive_store.exists(f"file://{tmp_path}", "nope.tar.zst")
    assert archive_store.size(f"file://{tmp_path}", "nope.tar.zst") == -1


# ── remote backend primitives ───────────────────────────────────


def test_store_open_exists_size_remote(tmp_path, mem):
    uri = "mem://vol"
    data = bytes(range(256)) * 30
    staging = tmp_path / "staging.upload"
    staging.write_bytes(data)

    archive_store.store(uri, "pkg.tar.zst", staging)

    assert not staging.exists()  # remote store consumes the staging file
    assert mem.objects["mem://vol/archives/pkg.tar.zst"] == data
    assert archive_store.exists(uri, "pkg.tar.zst")
    assert archive_store.size(uri, "pkg.tar.zst") == len(data)
    with archive_store.open_stream(uri, "pkg.tar.zst") as fh:
        assert fh.read() == data
    assert archive_store.sha256_of(uri, "pkg.tar.zst") == hashlib.sha256(data).hexdigest()


# ── copy_archive across backend types ───────────────────────────


def test_copy_archive_local_to_remote_and_back(tmp_path, mem):
    src = f"file://{tmp_path}"
    data = b"copy-me" * 2048
    (tmp_path / "archives").mkdir()
    (tmp_path / "archives" / "x.tar.zst").write_bytes(data)

    want = hashlib.sha256(data).hexdigest()

    # local -> remote, returns the streamed hash
    got = archive_store.copy_archive(src, "mem://vol", "x.tar.zst")
    assert got == want
    assert mem.objects["mem://vol/archives/x.tar.zst"] == data

    # remote -> a second local dir
    dst2 = tmp_path / "dst2"
    got2 = archive_store.copy_archive("mem://vol", f"file://{dst2}", "x.tar.zst")
    assert got2 == want
    assert (dst2 / "archives" / "x.tar.zst").read_bytes() == data
