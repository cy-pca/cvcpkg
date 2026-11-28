# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The CLI-login broker as a plain OAuth 2.0 authorization server.

An OAuth client that is not ``cvcpkg login`` drives the loopback grant here:
Ariadne's ``oidc-sign-in`` (transfix/libcvc ``src/cvc/ariadne/oidc_host.cpp``),
which cy-pca/cypkg signs in with.  These tests send exactly what it sends:

* discovery at ``/.well-known/openid-configuration``, whose ``issuer`` must be
  the one it was given, exactly, with every endpoint on the issuer's origin;
* ``/authorize`` with ``response_type=code``, ``scope``, ``state`` and PKCE
  S256, back to ``http://127.0.0.1:PORT/callback`` (or, in a browser page, the
  page itself), checking the reply's ``state`` and ``iss``;
* the code exchange and the refresh as form posts with ``client_id`` and no
  secret, wanting a ``Bearer`` ``access_token``;
* RFC 7009 revocation on sign-out (``token``, ``token_type_hint``, ``client_id``).

The browser sign-in at the identity provider is stood in for by the
token->session ``/login``, as in ``test_cli_login_broker.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

import cvcpkg.server.app as app_module
from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

PUBLIC = "https://registry.example"
PAGE = "https://cy-pca.github.io/cypkg/ari.html"
PAGE_ORIGIN = "https://cy-pca.github.io"
LOOPBACK = "http://127.0.0.1:53117/callback"


def _pkce(seed: str = "a"):
    verifier = (seed * 8) + "-long-enough-code-verifier-for-pkce-1234567890"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'oauth.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.delenv("CVCPKG_OAUTH_PAGE_REDIRECTS", raising=False)
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")
    monkeypatch.setenv("CVCPKG_PUBLIC_URL", PUBLIC + "/")
    monkeypatch.setattr(app_module, "CORS_ALLOWED_ORIGINS", [PAGE_ORIGIN])

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        admin = await DbTokenStore(tmp_path).create("oauth-admin", TokenRole.admin)
        await dispose_engine()
        return admin

    admin = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        resp = client.post("/login", data={"token": admin, "next": "/account"})
        assert resp.status_code in (200, 303), resp.text
        yield client


def _authorize(client, *, client_id="cypkg", redirect=LOOPBACK, scope="publisher", **extra):
    _, challenge = _pkce(extra.pop("seed", "a"))
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": scope,
        "state": "ariadne-state-0123456789abcdef",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **extra,
    }
    return client.get("/v1/auth/authorize", params=params, follow_redirects=False)


def _code(client, **kw):
    """Authorize, sign in, and return the redirect's query (code, state, iss)."""
    auth = _authorize(client, **kw)
    assert auth.status_code == 303, auth.text
    txn = auth.headers["location"].split("/v1/auth/resume/")[1]
    resume = client.get(f"/v1/auth/resume/{txn}", follow_redirects=False)
    assert resume.status_code == 302, resume.text
    target = resume.headers["location"]
    assert target.startswith(kw.get("redirect", LOOPBACK) + "?")
    return {k: v[0] for k, v in parse_qs(urlsplit(target).query).items()}


def _exchange(client, code, *, client_id="cypkg", redirect=LOOPBACK, seed="a", **headers):
    verifier, _ = _pkce(seed)
    return client.post(
        "/v1/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json", **headers},
    )


class TestMetadata:
    @pytest.mark.parametrize(
        "path", ["/.well-known/openid-configuration", "/.well-known/oauth-authorization-server"]
    )
    def test_document(self, env, path):
        doc = env.get(path).json()
        assert doc["issuer"] == PUBLIC  # CVCPKG_PUBLIC_URL, its trailing slash dropped
        for key in ("authorization_endpoint", "token_endpoint", "revocation_endpoint"):
            assert doc[key].startswith(PUBLIC + "/v1/auth/"), key
        assert doc["code_challenge_methods_supported"] == ["S256"]
        assert doc["response_types_supported"] == ["code"]
        assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert doc["token_endpoint_auth_methods_supported"] == ["none"]
        assert doc["scopes_supported"] == ["reader", "publisher", "admin"]
        assert doc["authorization_response_iss_parameter_supported"] is True
        # Not an OpenID provider: nothing that would promise ID tokens.
        assert "openid" not in doc["scopes_supported"]
        assert "jwks_uri" not in doc and "id_token_signing_alg_values_supported" not in doc
        # Nor RFC 8628: the device pairing is cvcpkg's own protocol.
        assert "device_authorization_endpoint" not in doc

    def test_issuer_is_where_it_was_reached_without_a_public_url(self, env, monkeypatch):
        monkeypatch.delenv("CVCPKG_PUBLIC_URL")
        doc = env.get("/.well-known/openid-configuration").json()
        assert doc["issuer"] == "http://testserver"
        assert doc["token_endpoint"] == "http://testserver/v1/auth/token"

    def test_any_origin_may_read_it(self, env):
        r = env.get(
            "/.well-known/openid-configuration", headers={"Origin": "https://elsewhere.example"}
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "*"


class TestLoopbackLikeAriadne:
    def test_sign_in_refresh_and_whoami(self, env):
        reply = _code(env)
        assert reply["state"] == "ariadne-state-0123456789abcdef"
        assert reply["iss"] == PUBLIC  # RFC 9207, as the metadata promises

        tok = _exchange(env, reply["code"])
        assert tok.status_code == 200, tok.text
        assert tok.headers["cache-control"] == "no-store"
        body = tok.json()
        assert body["token_type"].lower() == "bearer"
        assert body["access_token"].startswith("cvcses_")
        assert body["refresh_token"].startswith("cvcref_")
        assert body["expires_in"] > 0
        assert body["scope"] == "publisher"
        assert "id_token" not in body

        who = env.get(
            "/v1/auth/whoami", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert who.status_code == 200
        assert who.json()["role"] == "publisher" and who.json()["kind"] == "session"

        devices = env.get(
            "/v1/auth/devices", headers={"Authorization": f"Bearer {body['access_token']}"}
        ).json()["devices"]
        assert [d["client_id"] for d in devices if d["current"]] == ["cypkg"]

        # Ariadne refreshes with client_id and no secret.
        r = env.post(
            "/v1/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": body["refresh_token"],
                "client_id": "cypkg",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "publisher"
        assert r.json()["refresh_token"] != body["refresh_token"]

    def test_no_scope_is_the_floor(self, env):
        tok = _exchange(env, _code(env, scope="")["code"])
        assert tok.json()["scope"] == "reader"

    def test_role_wins_over_scope(self, env):
        tok = _exchange(env, _code(env, scope="admin", role="reader")["code"])
        assert tok.json()["scope"] == "reader"

    def test_openid_goes_back_to_the_client_as_invalid_scope(self, env):
        r = _authorize(env, scope="openid publisher")
        assert r.status_code == 302
        target = r.headers["location"]
        assert target.startswith(LOOPBACK + "?")
        q = {k: v[0] for k, v in parse_qs(urlsplit(target).query).items()}
        assert q["error"] == "invalid_scope"
        assert "no ID token" in q["error_description"]
        assert q["state"] == "ariadne-state-0123456789abcdef"
        assert q["iss"] == PUBLIC
        assert "code" not in q

    def test_other_response_types_go_back_as_unsupported(self, env):
        r = _authorize(env, response_type="token")
        q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
        assert r.status_code == 302 and q["error"] == "unsupported_response_type"

    def test_a_bad_redirect_never_gets_a_redirect(self, env):
        # Errors before the redirect URI is known good are shown, not sent there.
        r = _authorize(env, redirect="http://localhost:53117/callback", scope="openid")
        assert r.status_code == 400


class TestClientBinding:
    def test_code_is_redeemed_only_by_its_client(self, env):
        reply = _code(env)
        r = _exchange(env, reply["code"], client_id="cvcpkg-cli")
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

    def test_cli_default_client_still_works(self, env):
        # cvcpkg login always sends client_id; a request without one is the CLI's.
        reply = _code(env, client_id="cvcpkg-cli")
        verifier, _ = _pkce()
        r = env.post(
            "/v1/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": reply["code"],
                "redirect_uri": LOOPBACK,
                "code_verifier": verifier,
            },
        )
        assert r.status_code == 200, r.text

    def test_unknown_client_is_refused(self, env):
        r = _exchange(env, "whatever", client_id="not-a-client")
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"
        assert _authorize(env, client_id="not-a-client").status_code == 400

    def test_refresh_by_another_client_burns_nothing(self, env):
        body = _exchange(env, _code(env)["code"]).json()
        wrong = env.post(
            "/v1/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": body["refresh_token"],
                "client_id": "cvcpkg-cli",
            },
        )
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
        # Not burned, so not reuse: the right client still refreshes.
        right = env.post(
            "/v1/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": body["refresh_token"],
                "client_id": "cypkg",
            },
        )
        assert right.status_code == 200, right.text

    def test_device_pairing_stays_the_clis(self, env):
        r = env.post(
            "/v1/auth/device",
            json={"client_id": "cypkg", "device_label": "x", "verifier_hash": "0" * 64},
        )
        assert r.status_code == 400


class TestBrowserPage:
    def test_registered_page_is_the_redirect(self, env):
        reply = _code(env, redirect=PAGE)
        assert reply["iss"] == PUBLIC
        tok = _exchange(env, reply["code"], redirect=PAGE, Origin=PAGE_ORIGIN)
        assert tok.status_code == 200, tok.text
        # The page reads the reply: its origin is on CVCPKG_CORS_ORIGINS.
        assert tok.headers["access-control-allow-origin"] == PAGE_ORIGIN

    def test_an_unlisted_origin_cannot_read_the_tokens(self, env):
        reply = _code(env, redirect=PAGE)
        tok = _exchange(env, reply["code"], redirect=PAGE, Origin="https://elsewhere.example")
        assert "access-control-allow-origin" not in tok.headers

    def test_pages_are_per_client_and_exact(self, env):
        assert _authorize(env, client_id="cvcpkg-cli", redirect=PAGE).status_code == 400
        assert _authorize(env, redirect=PAGE + "?src=x.ari").status_code == 400
        assert _authorize(env, redirect="https://elsewhere.example/").status_code == 400

    def test_operator_registered_page(self, env, monkeypatch):
        page = PUBLIC + "/cypkg/"
        monkeypatch.setenv("CVCPKG_OAUTH_PAGE_REDIRECTS", f"cypkg={page}")
        reply = _code(env, redirect=page)
        assert _exchange(env, reply["code"], redirect=page).status_code == 200


class TestRevocation:
    def _signed_in(self, env):
        return _exchange(env, _code(env)["code"]).json()

    def _revoke(self, env, token, hint="refresh_token", client_id="cypkg"):
        data = {"token": token, "token_type_hint": hint}
        if client_id:
            data["client_id"] = client_id
        return env.post("/v1/auth/token/revoke", data=data)

    def _alive(self, env, access):
        return (
            env.get("/v1/auth/whoami", headers={"Authorization": f"Bearer {access}"}).status_code
            == 200
        )

    def test_revoking_the_refresh_ends_the_whole_sign_in(self, env):
        first = self._signed_in(env)
        second = env.post(
            "/v1/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": "cypkg",
            },
        ).json()
        assert self._alive(env, first["access_token"]) and self._alive(env, second["access_token"])
        r = self._revoke(env, second["refresh_token"])
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        # Every rotation of it goes: no access token of that sign-in lives on.
        assert not self._alive(env, first["access_token"])
        assert not self._alive(env, second["access_token"])
        again = env.post(
            "/v1/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": second["refresh_token"],
                "client_id": "cypkg",
            },
        )
        assert again.status_code == 400 and again.json()["error"] == "invalid_grant"

    def test_revoking_the_access_token_works_too(self, env):
        body = self._signed_in(env)
        assert self._revoke(env, body["access_token"], hint="access_token").status_code == 200
        assert not self._alive(env, body["access_token"])

    def test_unknown_or_dead_tokens_answer_200(self, env):
        assert self._revoke(env, "cvcref_nonsense").status_code == 200
        assert self._revoke(env, "not-a-session-token").status_code == 200
        body = self._signed_in(env)
        assert self._revoke(env, body["refresh_token"]).status_code == 200
        assert self._revoke(env, body["refresh_token"]).status_code == 200

    def test_another_clients_token_is_refused(self, env):
        body = self._signed_in(env)
        r = self._revoke(env, body["refresh_token"], client_id="cvcpkg-cli")
        assert r.status_code == 400 and r.json()["error"] == "unauthorized_client"
        assert self._alive(env, body["access_token"])

    def test_page_origin_may_revoke(self, env):
        body = self._signed_in(env)
        r = env.post(
            "/v1/auth/token/revoke",
            data={"token": body["refresh_token"], "client_id": "cypkg"},
            headers={"Origin": PAGE_ORIGIN},
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == PAGE_ORIGIN
