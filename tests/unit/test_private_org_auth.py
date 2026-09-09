# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Private-org install auth: the install-time catalog fetch and archive
download attach ``Authorization: Bearer $CVCPKG_TOKEN`` — but only to our own
server origin, never to the GitHub Pages fallback / mirrors / CDNs.

Regression: ``cvcpkg install <priv-org>/<pkg>`` failed with "no bundles found in
catalog for this platform tuple" (and, past that, a 404 on the archive) because
these two paths went through the tokenless storage backend while ``search``
alone sent the token.
"""

from __future__ import annotations

import urllib.request

import pytest

from cvcpkg import catalog, installer
from cvcpkg.backends.https import HttpsBackend
from cvcpkg.config import authorize_request

TOKEN = "cvctok_test_abcdef"
SERVER_URL = "https://cvcpkg.example"
SERVER_CATALOG = f"{SERVER_URL}/v1/catalog"
SERVER_ARCHIVE = f"{SERVER_URL}/v1/download/pkg-1.0.0-any-x86_64-release-shared.tar.zst"
# The proxy in front of the https server emits http:// download URLs.
SERVER_ARCHIVE_HTTP = (
    "http://cvcpkg.example/v1/download/pkg-1.0.0-any-x86_64-release-shared.tar.zst"
)
GITHUB_FALLBACK = "https://transfix.github.io/libcvc-deps/catalog/latest.yaml"
CDN_ARCHIVE = "https://cdn.example.net/artifacts/pkg.tar.zst"


# ── authorize_request: origin-scoping + scheme canonicalisation ─────


class TestAuthorizeRequest:
    def test_bearer_for_server_origin(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        assert authorize_request(SERVER_CATALOG) == (
            SERVER_CATALOG,
            {"Authorization": f"Bearer {TOKEN}"},
        )
        assert authorize_request(SERVER_ARCHIVE) == (
            SERVER_ARCHIVE,
            {"Authorization": f"Bearer {TOKEN}"},
        )

    def test_http_download_url_upgraded_to_https_before_auth(self, monkeypatch):
        # The regression that 404'd: the server (https) emits http:// archive
        # URLs behind its TLS proxy. We must upgrade to https AND then attach the
        # token — never send the bearer over cleartext http, never fetch the
        # private archive anonymously.
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        url, headers = authorize_request(SERVER_ARCHIVE_HTTP)
        assert url == SERVER_ARCHIVE  # scheme upgraded http -> https
        assert headers == {"Authorization": f"Bearer {TOKEN}"}

    def test_bearer_for_root_origin(self, monkeypatch):
        # A satellite: server is one host, the authoritative root another. The
        # token is valid for both of our servers.
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        monkeypatch.setenv("CVCPKG_ROOT_URL", "https://root.example")
        assert authorize_request("https://root.example/v1/catalog") == (
            "https://root.example/v1/catalog",
            {"Authorization": f"Bearer {TOKEN}"},
        )

    def test_no_token_no_header_but_scheme_still_canonical(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        assert authorize_request(SERVER_CATALOG) == (SERVER_CATALOG, {})
        # Still our server, so still canonicalised to https — just no credential.
        assert authorize_request(SERVER_ARCHIVE_HTTP) == (SERVER_ARCHIVE, {})

    def test_dev_server_http_on_custom_port_kept(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", "http://dev.local:8420")
        monkeypatch.setenv("CVCPKG_ROOT_URL", "http://dev.local:8420")
        url, headers = authorize_request("http://dev.local:8420/v1/download/x.tar.zst")
        assert url == "http://dev.local:8420/v1/download/x.tar.zst"
        assert headers == {"Authorization": f"Bearer {TOKEN}"}

    def test_never_leaks_to_github_fallback(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        # The security boundary: the public GitHub Pages catalog is a legitimate
        # fallback URL but a different host — unchanged URL, no token.
        assert authorize_request(GITHUB_FALLBACK) == (GITHUB_FALLBACK, {})

    def test_never_leaks_to_cdn_or_mirror(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        assert authorize_request(CDN_ARCHIVE) == (CDN_ARCHIVE, {})

    def test_userinfo_spoof_not_treated_as_server(self, monkeypatch):
        # http://cvcpkg.example@evil.net/... has host evil.net, not our server.
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        spoof = "https://cvcpkg.example@evil.net/v1/download/x.tar.zst"
        assert authorize_request(spoof) == (spoof, {})

    def test_non_http_scheme_ignored(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        assert authorize_request("file:///tmp/catalog.yaml") == ("file:///tmp/catalog.yaml", {})
        assert authorize_request("s3://bucket/pkg.tar.zst") == ("s3://bucket/pkg.tar.zst", {})

    def test_default_server_origin(self, monkeypatch):
        # With no override, the compiled-in default (cvcpkg.org) is our server.
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.delenv("CVCPKG_SERVER_URL", raising=False)
        monkeypatch.delenv("CVCPKG_ROOT_URL", raising=False)
        assert authorize_request("https://cvcpkg.org/v1/catalog") == (
            "https://cvcpkg.org/v1/catalog",
            {"Authorization": f"Bearer {TOKEN}"},
        )
        assert authorize_request(GITHUB_FALLBACK) == (GITHUB_FALLBACK, {})


# ── request capture harness ─────────────────────────────────────────


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0
        self.headers = {"Content-Length": str(len(body))}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._pos = self._body[self._pos :], len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


@pytest.fixture
def captured_requests(monkeypatch):
    """Patch urlopen so we can inspect the headers actually sent."""
    seen: list[urllib.request.Request] = []
    body = b"schema_version: 1\nrevision: 1\nbundles: []\n"

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        seen.append(req)
        return _FakeResp(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def _auth_values(reqs: list[urllib.request.Request]) -> list[str | None]:
    return [r.get_header("Authorization") for r in reqs]


# ── HttpsBackend merges the header over the default UA ──────────────


class TestHttpsBackendHeaders:
    def test_open_sends_extra_header_and_keeps_user_agent(self, captured_requests):
        HttpsBackend().open(SERVER_CATALOG, headers={"Authorization": f"Bearer {TOKEN}"}).read()
        (req,) = captured_requests
        assert req.get_header("Authorization") == f"Bearer {TOKEN}"
        assert req.get_header("User-agent", "").startswith("cvcpkg/")

    def test_open_without_headers_has_no_auth(self, captured_requests):
        HttpsBackend().open(SERVER_CATALOG).read()
        (req,) = captured_requests
        assert req.get_header("Authorization") is None


# ── catalog fetch attaches auth only for our origin ─────────────────


class TestCatalogFetchAuth:
    def test_server_catalog_is_authenticated(self, monkeypatch, captured_requests):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        catalog._fetch_url(SERVER_CATALOG)
        assert f"Bearer {TOKEN}" in _auth_values(captured_requests)

    def test_github_fallback_is_not_authenticated(self, monkeypatch, captured_requests):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        catalog._fetch_url(GITHUB_FALLBACK)
        assert _auth_values(captured_requests) == [None] * len(captured_requests)
        assert captured_requests  # sanity: a request WAS made


# ── archive download attaches auth only for our origin ──────────────


class TestArchiveDownloadAuth:
    def test_http_server_archive_upgraded_and_authenticated(
        self, monkeypatch, captured_requests, tmp_path
    ):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        # sha="" skips the integrity check so the fake body is accepted. Feed the
        # http:// URL the server actually emits and assert the real request went
        # out over https, with the bearer.
        installer._download_from_url(SERVER_ARCHIVE_HTTP, "pkg.tar.zst", "", tmp_path, 1 << 30)
        assert f"Bearer {TOKEN}" in _auth_values(captured_requests)
        assert captured_requests
        assert all(r.full_url.startswith("https://") for r in captured_requests)

    def test_cdn_archive_is_not_authenticated(self, monkeypatch, captured_requests, tmp_path):
        monkeypatch.setenv("CVCPKG_TOKEN", TOKEN)
        monkeypatch.setenv("CVCPKG_SERVER_URL", SERVER_URL)
        installer._download_from_url(CDN_ARCHIVE, "pkg.tar.zst", "", tmp_path, 1 << 30)
        assert _auth_values(captured_requests) == [None] * len(captured_requests)
        assert captured_requests
