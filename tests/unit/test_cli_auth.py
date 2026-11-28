# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Pure CLI-auth policy: loopback matcher, PKCE, challenge validation."""

from __future__ import annotations

import base64
import hashlib

import pytest

# cli_auth.py lives under cvcpkg.server and imports the DB rows + auth -> models
# -> pydantic, so it needs the server extras even for these pure-policy checks.
pytest.importorskip("pydantic", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="server extras not installed")

from cvcpkg.server import cli_auth


@pytest.mark.parametrize(
    "uri,ok",
    [
        ("http://127.0.0.1:49812/callback", True),
        ("http://[::1]:49812/callback", True),
        ("http://127.0.0.1:1024/callback", True),
        # localhost is DNS-resolvable and therefore steerable — refused.
        ("http://localhost:49812/callback", False),
        # https loopback is not how native apps do it (and we require http here).
        ("https://127.0.0.1:49812/callback", False),
        # a query string, wrong path, or sub-1024 port are all refused.
        ("http://127.0.0.1:49812/callback?x=1", False),
        ("http://127.0.0.1:49812/other", False),
        ("http://127.0.0.1:80/callback", False),
        ("http://8.8.8.8:49812/callback", False),
        ("not a uri", False),
    ],
)
def test_loopback_matcher(uri, ok):
    assert cli_auth.loopback_redirect_ok(uri) is ok


def test_pkce_s256():
    verifier = "the-verifier-value-used-by-the-client-1234567890"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert cli_auth.verify_pkce_s256(verifier, challenge)
    assert not cli_auth.verify_pkce_s256("wrong", challenge)
    assert not cli_auth.verify_pkce_s256(verifier, "")
    assert not cli_auth.verify_pkce_s256("", challenge)


def test_valid_code_challenge():
    good = base64.urlsafe_b64encode(hashlib.sha256(b"x").digest()).rstrip(b"=").decode()
    assert len(good) == 43
    assert cli_auth.valid_code_challenge(good)
    assert not cli_auth.valid_code_challenge("too-short")
    assert not cli_auth.valid_code_challenge("!" * 43)


def test_cli_clients_is_locked_down():
    assert set(cli_auth.CLI_CLIENTS) == {"cvcpkg-cli", "cypkg"}
    assert all(c["public"] is True for c in cli_auth.CLI_CLIENTS.values())
    # The device pairing stays the CLI's alone.
    assert [cid for cid, c in cli_auth.CLI_CLIENTS.items() if c.get("pairing")] == ["cvcpkg-cli"]
    # Only cypkg has browser pages, and only https ones.
    assert "pages" not in cli_auth.CLI_CLIENTS["cvcpkg-cli"]
    assert all(cli_auth.page_redirect_ok(u) for u in cli_auth.CLI_CLIENTS["cypkg"]["pages"])


@pytest.mark.parametrize("client_id", ["cvcpkg-cli", "cypkg"])
def test_every_client_may_use_a_loopback_callback(client_id, monkeypatch):
    monkeypatch.delenv(cli_auth.PAGE_REDIRECTS_ENV, raising=False)
    assert cli_auth.redirect_ok(client_id, "http://127.0.0.1:49812/callback")
    assert not cli_auth.redirect_ok(client_id, "http://localhost:49812/callback")


def test_pages_are_matched_exactly_and_per_client(monkeypatch):
    monkeypatch.delenv(cli_auth.PAGE_REDIRECTS_ENV, raising=False)
    page = "https://cy-pca.github.io/cypkg/ari.html"
    for client_page in ("arigl.html", "ari.html", "ari_tvision.html"):
        assert cli_auth.redirect_ok("cypkg", f"https://cy-pca.github.io/cypkg/{client_page}")
    # The CLI has no pages; an unknown client has nothing.
    assert not cli_auth.redirect_ok("cvcpkg-cli", page)
    assert not cli_auth.redirect_ok("someone-else", page)
    assert not cli_auth.redirect_ok("someone-else", "http://127.0.0.1:49812/callback")
    # Exact: another path, a query, another scheme or host are all different pages.
    for near in (
        "https://cy-pca.github.io/cypkg/",  # the landing page: it runs no client
        "https://cy-pca.github.io/cypkg",
        "https://cy-pca.github.io/cypkg/ari.html?src=evil.ari",
        "https://cy-pca.github.io/cypkg/index.html",
        "https://cy-pca.github.io/cypkg/other/",
        "http://cy-pca.github.io/cypkg/",
        "https://evil.github.io/cypkg/",
    ):
        assert not cli_auth.redirect_ok("cypkg", near), near


def test_operator_pages_extend_only_a_client_that_has_pages(monkeypatch):
    monkeypatch.setenv(
        cli_auth.PAGE_REDIRECTS_ENV,
        " cypkg=https://cvcpkg.example/cypkg/ , cvcpkg-cli=https://cvcpkg.example/cli/,"
        "cypkg=http://insecure.example/,cypkg=https://frag.example/#x,nonsense",
    )
    assert cli_auth.page_redirects("cypkg") == (
        "https://cy-pca.github.io/cypkg/arigl.html",
        "https://cy-pca.github.io/cypkg/ari.html",
        "https://cy-pca.github.io/cypkg/ari_tvision.html",
        "https://cvcpkg.example/cypkg/",
    )
    assert cli_auth.redirect_ok("cypkg", "https://cvcpkg.example/cypkg/")
    # Not for the CLI (it has no pages), and never a non-https or fragment page.
    assert not cli_auth.redirect_ok("cvcpkg-cli", "https://cvcpkg.example/cli/")
    assert not cli_auth.redirect_ok("cypkg", "http://insecure.example/")
    assert not cli_auth.redirect_ok("cypkg", "https://frag.example/#x")


@pytest.mark.parametrize(
    "scope,role",
    [
        ("", ""),
        ("reader", "reader"),
        ("publisher", "publisher"),
        ("reader admin", "admin"),
        ("publisher reader", "publisher"),
        # Unknown scopes are ignored (RFC 6749 §3.3), as is a role's case.
        ("offline_access profile", ""),
        ("Admin", ""),
    ],
)
def test_role_from_scope(scope, role):
    assert cli_auth.role_from_scope(scope) == role


def test_openid_scope_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="no ID token"):
        cli_auth.role_from_scope("openid reader")
