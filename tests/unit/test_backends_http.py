# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the stdlib HTTP backends (``https``, ``gh_release``) and the
remaining ``local`` edge branches.

All network I/O is mocked at ``urllib.request.urlopen``.  URLError failures
use a plain-string ``reason`` so :func:`cvcpkg.retry.is_transient` classifies
them as non-transient and ``with_retry`` re-raises immediately (no sleeps).
"""

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import cvcpkg
from cvcpkg.backends import gh_release, https, local
from cvcpkg.storage import ObjectInfo


class _FakeResponse:
    """Minimal stand-in for an ``http.client.HTTPResponse`` context manager."""

    def __init__(self, *, headers=None, body=b""):
        self.headers = headers or {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Headers(dict):
    """Header container whose ``.get`` mirrors ``email.message.Message``."""


# ════════════════════════════════════════════════════════════════
#  HTTPS / HTTP  (https.py)
# ════════════════════════════════════════════════════════════════


class TestUserAgent:
    def test_has_version(self):
        ua = https._user_agent()
        assert ua.startswith("cvcpkg/")
        assert ua != "cvcpkg/unknown"

    def test_unknown_when_version_missing(self, monkeypatch):
        # Force ``from cvcpkg import __version__`` to fail.
        monkeypatch.delattr(cvcpkg, "__version__", raising=False)
        assert https._user_agent() == "cvcpkg/unknown"


class TestHttpsHeaders:
    def test_default_only(self):
        hdrs = https.HttpsBackend._headers(None)
        assert set(hdrs) == {"User-Agent"}

    def test_merges_extra(self):
        hdrs = https.HttpsBackend._headers({"Authorization": "Bearer tok"})
        assert hdrs["Authorization"] == "Bearer tok"
        assert "User-Agent" in hdrs


class TestHttpsHead:
    def test_head_parses_headers(self, monkeypatch):
        resp = _FakeResponse(
            headers=_Headers(
                {"Content-Length": "1234", "ETag": '"e"', "Content-Type": "application/gzip"}
            )
        )
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: resp)
        info = https.HttpsBackend().head("https://example.com/a.tar.gz")
        assert info.size == 1234
        assert info.etag == '"e"'
        assert info.content_type == "application/gzip"

    def test_head_defaults_when_missing(self, monkeypatch):
        resp = _FakeResponse(headers=_Headers())
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: resp)
        info = https.HttpsBackend().head("https://example.com/a")
        assert info.size == -1
        assert info.etag == ""
        assert info.content_type == ""

    def test_head_sends_headers_and_method(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            captured["ua"] = req.get_header("User-agent")
            return _FakeResponse(headers=_Headers({"Content-Length": "1"}))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        https.HttpsBackend().head("https://example.com/a", headers={"X-Test": "1"})
        assert captured["method"] == "HEAD"
        assert captured["ua"].startswith("cvcpkg/")

    def test_head_urlerror_becomes_oserror(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("nope")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(OSError, match="HEAD https://example.com/a"):
            https.HttpsBackend().head("https://example.com/a")


class TestHttpsOpen:
    def test_open_returns_stream(self, monkeypatch):
        resp = _FakeResponse(body=b"payload")
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: resp)
        result = https.HttpsBackend().open("https://example.com/a")
        assert result.read() == b"payload"

    def test_open_urlerror_becomes_oserror(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(OSError, match="GET https://example.com/a"):
            https.HttpsBackend().open("https://example.com/a")


class TestHttpsSupportsRange:
    def test_true_when_size_positive(self, monkeypatch):
        monkeypatch.setattr(
            https.HttpsBackend, "head", lambda self, uri: ObjectInfo(size=10)
        )
        assert https.HttpsBackend().supports_range("https://x/y") is True

    def test_false_when_size_zero(self, monkeypatch):
        monkeypatch.setattr(
            https.HttpsBackend, "head", lambda self, uri: ObjectInfo(size=0)
        )
        assert https.HttpsBackend().supports_range("https://x/y") is False

    def test_false_on_oserror(self, monkeypatch):
        def boom(self, uri):
            raise OSError("head failed")

        monkeypatch.setattr(https.HttpsBackend, "head", boom)
        assert https.HttpsBackend().supports_range("https://x/y") is False


class TestHttpsPut:
    def test_put_success(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            captured["clen"] = req.get_header("Content-length")
            captured["ctype"] = req.get_header("Content-type")
            captured["body"] = req.data
            return _FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        https.HttpsBackend().put("https://example.com/a", io.BytesIO(b"upload"), size=6)
        assert captured["method"] == "PUT"
        assert captured["clen"] == "6"
        assert captured["ctype"] == "application/octet-stream"
        assert captured["body"] == b"upload"

    def test_put_without_size_omits_content_length(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["clen"] = req.get_header("Content-length")
            return _FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        https.HttpsBackend().put("https://example.com/a", io.BytesIO(b"x"))
        assert captured["clen"] is None

    def test_put_urlerror_becomes_oserror(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(OSError, match="PUT https://example.com/a"):
            https.HttpsBackend().put("https://example.com/a", io.BytesIO(b"x"))


# ════════════════════════════════════════════════════════════════
#  GitHub Releases  (gh_release.py)
# ════════════════════════════════════════════════════════════════


class TestGhParse:
    def test_parse(self):
        owner, repo, tag, asset = gh_release._parse_gh_uri("gh-release://o/r/v1.0/pkg.tar.gz")
        assert (owner, repo, tag, asset) == ("o", "r", "v1.0", "pkg.tar.gz")

    def test_parse_asset_with_slashes(self):
        _, _, _, asset = gh_release._parse_gh_uri("gh-release://o/r/v1/dir/pkg.tar.gz")
        assert asset == "dir/pkg.tar.gz"

    def test_parse_too_few_parts(self):
        with pytest.raises(ValueError, match="gh-release://owner/repo/tag/asset"):
            gh_release._parse_gh_uri("gh-release://o/r/v1")


class TestGhHeaders:
    def test_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        hdrs = gh_release._gh_headers()
        assert hdrs == {"Accept": "application/vnd.github+json"}

    def test_github_token(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "gh-abc")
        assert gh_release._gh_headers()["Authorization"] == "Bearer gh-abc"

    def test_gh_token_fallback(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "gh-xyz")
        assert gh_release._gh_headers()["Authorization"] == "Bearer gh-xyz"


def _release_json(assets):
    return json.dumps({"assets": assets}).encode()


class TestResolveAssetUrl:
    def test_found(self, monkeypatch):
        body = _release_json(
            [
                {"name": "other.bin", "browser_download_url": "http://x/other", "size": 1},
                {"name": "pkg.tar.gz", "browser_download_url": "http://cdn/pkg", "size": 42},
            ]
        )
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(body=body)
        )
        url, size = gh_release._resolve_asset_url("o", "r", "v1", "pkg.tar.gz")
        assert url == "http://cdn/pkg"
        assert size == 42

    def test_not_found(self, monkeypatch):
        body = _release_json([{"name": "a", "browser_download_url": "u", "size": 1}])
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(body=body)
        )
        with pytest.raises(FileNotFoundError, match="Asset 'missing' not found"):
            gh_release._resolve_asset_url("o", "r", "v1", "missing")

    def test_api_urlerror_becomes_oserror(self, monkeypatch):
        def boom(req, timeout=None):
            raise urllib.error.URLError("api down")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(OSError, match="GitHub API error"):
            gh_release._resolve_asset_url("o", "r", "v1", "a")


class TestGhReleaseBackend:
    def test_schemes_and_supports_range(self):
        assert gh_release.GhReleaseBackend.schemes == ("gh-release",)
        assert gh_release.GhReleaseBackend().supports_range("gh-release://o/r/v/a") is True

    def test_head(self, monkeypatch):
        monkeypatch.setattr(
            gh_release, "_resolve_asset_url", lambda *a: ("http://cdn/pkg", 99)
        )
        info = gh_release.GhReleaseBackend().head("gh-release://o/r/v1/pkg")
        assert info.size == 99

    def test_open_returns_stream(self, monkeypatch):
        monkeypatch.setattr(
            gh_release, "_resolve_asset_url", lambda *a: ("http://cdn/pkg", 99)
        )
        resp = _FakeResponse(body=b"asset-bytes")
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: resp)
        result = gh_release.GhReleaseBackend().open("gh-release://o/r/v1/pkg")
        assert result.read() == b"asset-bytes"

    def test_open_urlerror_becomes_oserror(self, monkeypatch):
        monkeypatch.setattr(
            gh_release, "_resolve_asset_url", lambda *a: ("http://cdn/pkg", 99)
        )

        def boom(url, timeout=None):
            raise urllib.error.URLError("cdn down")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(OSError, match="Failed to download"):
            gh_release.GhReleaseBackend().open("gh-release://o/r/v1/pkg")


# ════════════════════════════════════════════════════════════════
#  local.py remaining edge branches
# ════════════════════════════════════════════════════════════════


class TestLocalEdges:
    def test_plain_path_without_scheme(self):
        # No scheme at all -> Path(uri) directly.  A drive-less POSIX-style
        # path has an empty scheme on both Windows and Linux.
        p = local._uri_to_path("/plain/local/path")
        assert p == Path("/plain/local/path")

    def test_wrong_scheme_raises(self):
        with pytest.raises(ValueError, match="does not handle scheme 'https'"):
            local._uri_to_path("https://example.com/x")

    def test_network_host_netloc_only(self):
        # file://server with no path -> Path(netloc).
        p = local._uri_to_path("file://server")
        assert str(p).endswith("server")

    def test_supports_range_true(self):
        assert local.FileBackend().supports_range("file:///tmp/x") is True

    def test_list_nonexistent_dir_returns_empty(self, tmp_path):
        assert local.FileBackend().list(f"file://{tmp_path}/nope") == []

    def test_put_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "nested" / "deep" / "out.bin"
        local.FileBackend().put(f"file://{dest}", io.BytesIO(b"data"))
        assert dest.read_bytes() == b"data"


# ── Registry dispatch ───────────────────────────────────────────


class TestDispatch:
    def test_get_backend_https(self):
        from cvcpkg.storage import get_backend

        assert "https" in get_backend("https://example.com/x").schemes

    def test_get_backend_gh_release(self):
        from cvcpkg.storage import get_backend

        assert "gh-release" in get_backend("gh-release://o/r/v/a").schemes

    def test_get_backend_file_default_scheme(self):
        from cvcpkg.storage import get_backend

        # No scheme -> defaults to "file".
        assert "file" in get_backend("/plain/local/path").schemes
