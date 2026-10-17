# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The client's catalog fetch: ETag revalidation, gzip, and JSON parsing.

Every ``cvcpkg install`` fetched the registry's ~7 MB JSON catalog in full,
uncompressed, and parsed it with ``yaml.safe_load`` (19 s on a prod-sized
catalog; ``json.loads`` takes 60 ms).  It now asks for gzip, keeps the body
with its ETag under the cache dir, revalidates with ``If-None-Match``, and
parses JSON as JSON.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import json
import urllib.error
import urllib.request

import pytest
import yaml

from cvcpkg import cache as cache_mod
from cvcpkg import catalog
from cvcpkg.errors import CatalogError, IntegrityError

SERVER_URL = "https://cvcpkg.example"
CATALOG_URL = f"{SERVER_URL}/v1/catalog"
FALLBACK_URL = "https://transfix.github.io/libcvc-deps/catalog/latest.yaml"


def _catalog(*names: str) -> dict:
    return {
        "schema_version": 1,
        "revision": len(names),
        "bundles": [
            {"name": n, "version": "1.0", "platform": "linux", "arch": "x86_64"} for n in names
        ],
    }


class _Resp:
    """Enough of an ``http.client.HTTPResponse`` for the https backend."""

    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200):
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = http.client.HTTPMessage()
        for k, v in headers.items():
            self.headers[k] = v

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRegistry:
    """A /v1/catalog that answers like the cvcpkg server: ETag, 304, gzip."""

    def __init__(self, monkeypatch):
        self.requests: list[urllib.request.Request] = []
        self.content_type = "application/json"
        self.publish(_catalog("zlib"))
        monkeypatch.setattr("urllib.request.urlopen", self.urlopen)

    def publish(self, cat: dict | bytes, etag: str | None = None) -> None:
        self.body = cat if isinstance(cat, bytes) else json.dumps(cat).encode()
        self.etag = etag or f'"{hashlib.sha256(self.body).hexdigest()[:16]}"'

    def gets(self) -> list[urllib.request.Request]:
        return [r for r in self.requests if r.get_method() == "GET"]

    def urlopen(self, req, timeout=None):  # noqa: ARG002
        self.requests.append(req)
        if req.get_method() == "HEAD":
            return _Resp(b"", {})
        inm = req.get_header("If-none-match")
        if inm and inm == self.etag:
            raise urllib.error.HTTPError(
                req.full_url, 304, "Not Modified", _Resp(b"", {"ETag": self.etag}).headers, None
            )
        headers = {"ETag": self.etag, "Content-Type": self.content_type}
        body = self.body
        if "gzip" in (req.get_header("Accept-encoding") or ""):
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(body))
        return _Resp(body, headers)


@pytest.fixture()
def registry(monkeypatch):
    monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    return FakeRegistry(monkeypatch)


def _names(cat: dict) -> list[str]:
    return [b["name"] for b in cat["bundles"]]


class TestRevalidation:
    def test_second_fetch_revalidates_and_reuses_the_kept_copy(self, registry, tmp_path):
        first = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert _names(first) == ["zlib"]
        (req,) = registry.gets()
        assert req.get_header("If-none-match") is None
        assert req.get_header("Accept-encoding") == "gzip"

        second = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert second == first
        assert registry.gets()[-1].get_header("If-none-match") == registry.etag

    def test_changed_catalog_is_downloaded_and_kept(self, registry, tmp_path):
        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        old_tag = registry.etag
        registry.publish(_catalog("zlib", "bzip2"))

        cat = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert _names(cat) == ["zlib", "bzip2"]
        assert registry.gets()[-1].get_header("If-none-match") == old_tag

        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert registry.gets()[-1].get_header("If-none-match") == registry.etag

    def test_without_a_cache_dir_nothing_is_conditional(self, registry):
        catalog.fetch_catalog(CATALOG_URL)
        catalog.fetch_catalog(CATALOG_URL)
        assert [r.get_header("If-none-match") for r in registry.gets()] == [None, None]

    def test_a_token_never_revalidates_another_tokens_copy(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", "cvctok_member")
        registry.publish(_catalog("zlib", "secret"))
        member = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert _names(member) == ["zlib", "secret"]

        monkeypatch.setenv("CVCPKG_TOKEN", "cvctok_outsider")
        registry.publish(_catalog("zlib"))
        outsider = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert _names(outsider) == ["zlib"]
        last = registry.gets()[-1]
        assert last.get_header("If-none-match") is None
        assert last.get_header("Authorization") == "Bearer cvctok_outsider"

        # Nor does the anonymous scope.
        monkeypatch.delenv("CVCPKG_TOKEN")
        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert registry.gets()[-1].get_header("If-none-match") is None

    def test_cache_holds_no_token(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", "cvctok_secret_value")
        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        files = sorted((tmp_path / "catalog").iterdir())
        assert [f.suffix for f in files] == [".body", ".json"]
        for f in files:
            assert b"cvctok_secret_value" not in f.read_bytes()
            assert "cvctok_secret_value" not in f.name

    def test_corrupt_copy_is_not_revalidated(self, registry, tmp_path):
        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        (body,) = (tmp_path / "catalog").glob("*.body")
        body.write_bytes(b'{"schema_version": 1, "bundles": []}')

        cat = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert _names(cat) == ["zlib"]
        assert registry.gets()[-1].get_header("If-none-match") is None

    def test_expected_sha256_checks_the_reused_copy(self, registry, tmp_path):
        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        good = hashlib.sha256(registry.body).hexdigest()
        assert catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path, expected_sha256=good)
        with pytest.raises(IntegrityError):
            catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path, expected_sha256="0" * 64)

    def test_unasked_304_falls_back(self, registry, tmp_path, monkeypatch):
        def always_304(req, timeout=None):  # noqa: ARG001
            registry.requests.append(req)
            if req.get_method() == "HEAD":
                return _Resp(b"", {})
            if req.full_url == CATALOG_URL:
                raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", None, None)
            return _Resp(yaml.safe_dump(_catalog("fallback")).encode(), {})

        monkeypatch.setattr("urllib.request.urlopen", always_304)
        cat = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path, fallback_urls=[FALLBACK_URL])
        assert _names(cat) == ["fallback"]

    def test_gc_still_clears_the_catalog_copies(self, registry, tmp_path):
        # `cvcpkg gc` removes every cache entry that is not a referenced
        # archive hash, unlinking the files inside; a subdirectory there
        # would crash it.
        catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert all(p.is_file() for p in (tmp_path / "catalog").iterdir())
        assert cache_mod.gc(tmp_path, set()) == 1
        assert not (tmp_path / "catalog").exists()


class TestEncodingAndParsing:
    def test_gzip_body_is_decompressed(self, registry, tmp_path):
        registry.publish(_catalog("zlib", "bzip2"))
        cat = catalog.fetch_catalog(CATALOG_URL, cache_dir=tmp_path)
        assert _names(cat) == ["zlib", "bzip2"]
        # The kept copy is the decoded body, not the gzip bytes.
        (body,) = (tmp_path / "catalog").glob("*.body")
        assert json.loads(body.read_bytes()) == cat

    def test_json_is_not_parsed_as_yaml(self, registry, monkeypatch):
        def no_yaml(*a, **kw):
            raise AssertionError("a JSON catalog went through yaml.safe_load")

        monkeypatch.setattr(catalog.yaml, "safe_load", no_yaml)
        assert _names(catalog.fetch_catalog(CATALOG_URL)) == ["zlib"]

    def test_yaml_catalog_still_parses(self, registry):
        registry.content_type = "text/yaml"
        registry.publish(yaml.safe_dump(_catalog("zlib", "boost")).encode())
        assert _names(catalog.fetch_catalog(CATALOG_URL)) == ["zlib", "boost"]

    @pytest.mark.parametrize(
        "body, content_type",
        [
            (b'{"schema_version": 1, "bundles": []}', ""),
            (b'  \n{"schema_version": 1, "bundles": []}', "text/plain"),
            (b"schema_version: 1\nbundles: []\n", "application/json"),  # mislabelled
            (b"{schema_version: 1, bundles: []}", ""),  # YAML flow mapping
        ],
    )
    def test_parse_catalog(self, body, content_type):
        assert catalog._parse_catalog(body, content_type) == {
            "schema_version": 1,
            "bundles": [],
        }

    def test_gzip_bomb_is_refused(self):
        bomb = gzip.compress(b"0" * (2 * 1024 * 1024))
        with pytest.raises(CatalogError, match="exceeds"):
            catalog._gunzip(bomb, CATALOG_URL, 1024 * 1024)

    def test_truncated_gzip_is_a_transport_fault(self):
        from cvcpkg.retry import is_transient

        cut = gzip.compress(json.dumps(_catalog(*"abcdefgh")).encode())[:-12]
        with pytest.raises(http.client.IncompleteRead) as info:
            catalog._gunzip(cut, CATALOG_URL, 1 << 20)
        assert is_transient(info.value)

    def test_file_urls_get_no_http_headers(self, tmp_path):
        # LocalBackend.open takes no headers: the gzip/ETag headers are HTTP only.
        p = tmp_path / "catalog.json"
        p.write_text(json.dumps(_catalog("local")))
        cat = catalog.fetch_catalog(p.as_uri(), cache_dir=tmp_path / "cache")
        assert _names(cat) == ["local"]
