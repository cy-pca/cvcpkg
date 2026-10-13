# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Archive downloads that come back short or wrong.

On 2026-10-12 a production deploy replaced the server's container while CI
jobs were downloading from it.  Every in-flight body was cut off at a 64 KiB
chunk boundary, ``http.client`` reported the cut as an ordinary EOF, and the
client hashed the partial file: "sha256 mismatch" for python311 (65,536 of
81,631,492 bytes) and emsdk (40,632,320 of 575,555,642), never retried.

These drive the real download path -- ``download_bundle`` through
``HttpsBackend`` -- over real ``http.client.HTTPResponse`` objects parsed from
raw bytes, so the premature-EOF behaviour under test is the stdlib's own.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cvcpkg import installer
from cvcpkg.backends.https import HttpsBackend, TruncatedBody
from cvcpkg.errors import DownloadIntegrityError, InstallError, IntegrityError
from cvcpkg.manifest import CatalogEntry
from cvcpkg.retry import is_transient

FILENAME = "zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz"
URL = f"https://pkg.example.test/v1/download/{FILENAME}"


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # Incompressible content, so the archive spans several 64 KiB reads.
        data = b"".join(hashlib.sha256(i.to_bytes(4, "big")).digest() for i in range(8192))
        info = tarfile.TarInfo("lib/libz.so")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


GOOD = _archive()
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()


class _Sock:
    """Just enough socket for ``http.client.HTTPResponse`` to parse from."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def makefile(self, mode: str, *args, **kwargs) -> io.BufferedReader:
        return io.BufferedReader(io.BytesIO(self._raw))


def _response(body: bytes, *, content_length: int, method: str = "GET"):
    """A real HTTPResponse whose connection ends after *body*, whatever
    Content-Length promised -- exactly what a killed upstream looks like."""
    head = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Length: {content_length}\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    resp = http.client.HTTPResponse(_Sock(head + body), method=method)
    resp.begin()
    return resp


class _Server:
    """Serves the queued GET bodies in order (the last one repeats)."""

    def __init__(self, *bodies: tuple[bytes, int]) -> None:
        self.bodies = list(bodies)
        self.gets: list[urllib.request.Request] = []

    def urlopen(self, req, timeout=None):  # noqa: ARG002
        if req.get_method() == "HEAD":
            return _response(b"", content_length=len(GOOD), method="HEAD")
        self.gets.append(req)
        body, clen = self.bodies[0] if len(self.bodies) == 1 else self.bodies.pop(0)
        return _response(body, content_length=clen)


@pytest.fixture
def slept(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))
    return waits


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    monkeypatch.setenv("CVCPKG_SERVER_URL", "https://cvcpkg.example.test")


def _serve(monkeypatch, *bodies: tuple[bytes, int]) -> _Server:
    server = _Server(*bodies)
    monkeypatch.setattr(urllib.request, "urlopen", server.urlopen)
    return server


def _entry(sha: str = GOOD_SHA) -> CatalogEntry:
    return CatalogEntry(
        name="zlib",
        version="1.3.1+cvc.1",
        upstream_version="1.3.1",
        cvc_revision=1,
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256=sha,
        size_bytes=len(GOOD),
        archive_url=URL,
        source_release="",
    )


def _cache_files(cache: Path) -> list[str]:
    return sorted(str(p.relative_to(cache)) for p in cache.rglob("*") if p.is_file())


TRUNCATED = (GOOD[: 1 << 16], len(GOOD))  # first chunk only, as python311 got
WRONG = (bytes(reversed(GOOD)), len(GOOD))  # complete, but other bytes
OK = (GOOD, len(GOOD))


# ── the transport: a short body is an error, not an EOF ─────────────────────


def test_stdlib_reports_a_cut_connection_as_eof():
    """The premise.  If CPython ever starts raising here, TruncatedBody is
    merely redundant -- but as of 3.12 it silently returns b''."""
    resp = _response(GOOD[:100], content_length=len(GOOD))
    assert resp.read(1 << 16) == GOOD[:100]
    assert resp.read(1 << 16) == b""


def test_https_body_raises_when_it_ends_short_of_content_length(monkeypatch):
    _serve(monkeypatch, TRUNCATED)
    with HttpsBackend().open(URL) as body:
        assert body.read(1 << 16) == GOOD[: 1 << 16]
        with pytest.raises(TruncatedBody) as ei:
            body.read(1 << 16)
    assert ei.value.received == 1 << 16
    assert ei.value.content_length == len(GOOD)
    assert f"connection closed after {1 << 16} of {len(GOOD)} bytes" in str(ei.value)
    # ...and that is a transport fault, which the retry policy asks again.
    assert is_transient(ei.value)
    wrapped = InstallError(f"failed to download {URL}: {ei.value}")
    wrapped.__cause__ = ei.value
    assert is_transient(wrapped)


def test_https_body_exposes_status_and_length(monkeypatch):
    _serve(monkeypatch, OK)
    with HttpsBackend().open(URL) as body:
        assert body.status == 200
        assert body.content_length == len(GOOD)
        assert body.read() == GOOD


def test_a_sha_mismatch_is_still_not_transient():
    """with_retry must never swallow a mismatch, subclass included; asking
    again is download_bundle's own, separately bounded decision."""
    err = DownloadIntegrityError(FILENAME, "a" * 64, "b" * 64)
    assert isinstance(err, IntegrityError)
    assert not is_transient(err)


# ── first body bad, second good ─────────────────────────────────────────────


def test_truncated_body_then_good_one(tmp_path, monkeypatch, slept):
    server = _serve(monkeypatch, TRUNCATED, OK)
    path = installer.download_bundle(_entry(), tmp_path)

    assert path.read_bytes() == GOOD
    assert path == tmp_path / GOOD_SHA / FILENAME
    assert len(server.gets) == 2
    # Nothing but the verified archive is left in the cache.
    assert _cache_files(tmp_path) == [f"{GOOD_SHA}/{FILENAME}"]


def test_wrong_body_then_good_one(tmp_path, monkeypatch, slept, caplog):
    server = _serve(monkeypatch, WRONG, OK)
    with caplog.at_level("WARNING", logger="cvcpkg"):
        path = installer.download_bundle(_entry(), tmp_path)

    assert path.read_bytes() == GOOD
    assert len(server.gets) == 2
    assert _cache_files(tmp_path) == [f"{GOOD_SHA}/{FILENAME}"]
    # It backed off before fetching again, and said so.
    assert slept and slept[0] >= installer._INTEGRITY_BASE_DELAY_S
    assert any("sha256 mismatch" in r.getMessage() for r in caplog.records)
    assert any("from scratch" in r.getMessage() for r in caplog.records)


def test_retries_start_from_byte_zero(tmp_path, monkeypatch, slept):
    """No resume on top of a bad partial file: every attempt is a plain GET."""
    server = _serve(monkeypatch, TRUNCATED, WRONG, OK)
    installer.download_bundle(_entry(), tmp_path)
    assert len(server.gets) == 3
    assert all(r.get_header("Range") is None for r in server.gets)


# ── persistently bad ────────────────────────────────────────────────────────


def test_persistently_wrong_body_fails_after_bounded_attempts(tmp_path, monkeypatch, slept):
    server = _serve(monkeypatch, WRONG)
    with pytest.raises(DownloadIntegrityError) as ei:
        installer.download_bundle(_entry(), tmp_path)

    assert len(server.gets) == installer.INTEGRITY_ATTEMPTS
    assert len(slept) == installer.INTEGRITY_ATTEMPTS - 1
    msg = str(ei.value)
    wrong_sha = hashlib.sha256(WRONG[0]).hexdigest()
    assert msg.startswith(f"sha256 mismatch for {FILENAME}: expected {GOOD_SHA}, got {wrong_sha}")
    assert f"{installer.INTEGRITY_ATTEMPTS} attempts" in msg
    assert "the same wrong sha256 each time" in msg
    assert "HTTP 200" in msg
    assert f"Content-Length {len(GOOD)}" in msg
    assert f"received {len(GOOD)} bytes" in msg
    assert URL in msg
    assert ei.value.attempts == installer.INTEGRITY_ATTEMPTS
    # The rejected bytes were never cached.
    assert _cache_files(tmp_path) == []


def test_persistently_truncated_body_fails_with_the_byte_counts(tmp_path, monkeypatch, slept):
    server = _serve(monkeypatch, TRUNCATED)
    with pytest.raises(InstallError) as ei:
        installer.download_bundle(_entry(), tmp_path)

    assert not isinstance(ei.value, IntegrityError)
    assert len(server.gets) == installer._TRANSFER_ATTEMPTS
    msg = str(ei.value)
    assert f"connection closed after {1 << 16} of {len(GOOD)} bytes" in msg
    assert f"gave up after {installer._TRANSFER_ATTEMPTS} download attempts" in msg
    assert _cache_files(tmp_path) == []


def test_a_mismatch_is_never_extracted(tmp_path, monkeypatch, slept):
    _serve(monkeypatch, WRONG)
    prefix = tmp_path / "prefix"
    with pytest.raises(IntegrityError):
        installer.install_entry(_entry(), prefix, tmp_path / "cache")
    assert not prefix.exists() or not any(prefix.iterdir())
    assert _cache_files(tmp_path / "cache") == []


def test_a_bad_cache_entry_is_not_trusted(tmp_path, monkeypatch, slept):
    """A corrupt file under the right sha256 directory is re-downloaded,
    not installed: the cache re-verifies what it holds."""
    bad = tmp_path / GOOD_SHA / FILENAME
    bad.parent.mkdir(parents=True)
    bad.write_bytes(GOOD[: 1 << 16])
    server = _serve(monkeypatch, OK)
    path = installer.download_bundle(_entry(), tmp_path)
    assert path.read_bytes() == GOOD
    assert len(server.gets) == 1


def test_a_down_server_is_not_hammered_by_integrity_retries(tmp_path, monkeypatch, slept):
    """A transfer that never succeeds is the transfer budget's call alone;
    the integrity loop only re-runs after a complete, wrong body."""
    calls: list[str] = []

    def unavailable(req, timeout=None):  # noqa: ARG001
        calls.append(req.get_method())
        raise urllib.error.HTTPError(URL, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    with pytest.raises(InstallError) as ei:
        installer.download_bundle(_entry(), tmp_path)
    assert calls.count("GET") == 1  # a 404 is an answer
    assert "gave up after" not in str(ei.value)
    assert _cache_files(tmp_path) == []


# ── the catalog rides the same transport ────────────────────────────────────


def test_truncated_catalog_is_fetched_again(monkeypatch, slept):
    """A YAML catalog cut at a line boundary can still parse -- minus the
    bundles it lost -- so a short catalog body must be refetched, not parsed."""
    from cvcpkg import catalog

    doc = b"schema_version: 1\nrevision: 7\nbundles: []\n"
    cut = doc[: doc.index(b"bundles")]  # "schema_version: 1\nrevision: 7\n" parses fine
    server = _serve(monkeypatch, (cut, len(doc)), (doc, len(doc)))
    data = catalog.fetch_catalog("https://cvcpkg.example.test/v1/catalog")
    assert data == {"schema_version": 1, "revision": 7, "bundles": []}
    assert len(server.gets) == 2
