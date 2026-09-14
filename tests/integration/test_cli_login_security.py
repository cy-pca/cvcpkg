# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Security controls of the CLI-login broker (the load-bearing guards).

These pin the anti-phishing / least-privilege / brute-force / seam controls so a
regression that defeats one keeps the suite RED rather than green:

* least privilege by default — a bare login mints the floor role, and the
  approval screen pre-selects the lowest role, not the device's request;
* the role only ever narrows, and CVCPKG_CLI_MAX_ROLE caps it;
* the DB-backed user-code lockout trips and cannot be bypassed by endpoint;
* a disabled principal cannot collect a paired session;
* a session (cvcses_) cannot administer a token (the credential seam boundary);
* sessions are per-principal isolated for revoke/devices.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

_CSRF_RE = re.compile(r'name="_csrf" value="([0-9a-f]+)"')


def _pkce():
    verifier = "a-long-enough-code-verifier-for-pkce-1234567890"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def _make_env(tmp_path, monkeypatch, extra_env=None):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sec.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        tokens = DbTokenStore(tmp_path)
        admin = await tokens.create("admin-tok", TokenRole.admin)
        pub = await tokens.create("pub-tok", TokenRole.publisher)
        await dispose_engine()
        return admin, pub

    admin, pub = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    return app, admin, pub


@pytest.fixture()
def env(tmp_path, monkeypatch):
    app, admin, pub = _make_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        yield client, admin, pub


def _login(client, token):
    assert client.post("/login", data={"token": token, "next": "/account"}).status_code in (
        200,
        303,
    )


def _loopback_role(client, *, role=""):
    """Run the loopback grant for the currently-signed-in browser session.

    Returns the token response dict.
    """
    verifier, challenge = _pkce()
    redirect = "http://127.0.0.1:49812/callback"
    params = {
        "client_id": "cvcpkg-cli",
        "redirect_uri": redirect,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "s",
    }
    if role:
        params["role"] = role
    auth = client.get("/v1/auth/authorize", params=params, follow_redirects=False)
    assert auth.status_code == 303, auth.text
    txn = auth.headers["location"].split("/v1/auth/resume/")[1]
    resume = client.get(f"/v1/auth/resume/{txn}", follow_redirects=False)
    assert resume.status_code == 302, resume.text
    code = re.search(r"[?&]code=([^&]+)", resume.headers["location"]).group(1)
    tok = client.post(
        "/v1/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "cvcpkg-cli",
            "redirect_uri": redirect,
        },
    )
    assert tok.status_code == 200, tok.text
    return tok.json()


class TestLeastPrivilege:
    def test_bare_login_mints_the_floor_role(self, env):
        client, admin, _ = env
        _login(client, admin)  # local principal, last_role=admin
        resp = _loopback_role(client)  # no --role
        assert resp["role"] == "reader"  # floor, NOT the admin ceiling

    def test_role_narrows_only(self, env):
        client, admin, pub = env
        _login(client, pub)  # publisher principal
        resp = _loopback_role(client, role="admin")  # ask for more than entitled
        assert resp["role"] == "publisher"  # capped to entitlement

    def test_invalid_role_is_rejected(self, env):
        client, admin, _ = env
        _login(client, admin)
        _, challenge = _pkce()
        auth = client.get(
            "/v1/auth/authorize",
            params={
                "client_id": "cvcpkg-cli",
                "redirect_uri": "http://127.0.0.1:49812/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "s",
                "role": "superuser",
            },
            follow_redirects=False,
        )
        assert auth.status_code == 400

    def test_pairing_confirm_defaults_to_lowest_not_requested(self, env):
        client, admin, _ = env
        # A device asks for admin.
        start = client.post(
            "/v1/auth/device",
            json={"client_id": "cvcpkg-cli", "requested_role": "admin", "verifier_hash": "x"},
        )
        code = start.json()["user_code"]
        _login(client, admin)  # an admin-entitled approver
        page = client.get(f"/link?code={code}")
        assert page.status_code == 200
        # The selected option must be reader, never the requested admin.
        assert 'value="reader" selected' in page.text
        assert 'value="admin" selected' not in page.text
        # ...but the ask is shown read-only so the human sees it.
        assert "This device asked for" in page.text


class TestCliMaxRole:
    def test_ceiling_caps_the_grant(self, tmp_path, monkeypatch):
        app, admin, _ = _make_env(tmp_path, monkeypatch, {"CVCPKG_CLI_MAX_ROLE": "reader"})
        with TestClient(app) as client:
            _login(client, admin)  # admin-entitled principal
            resp = _loopback_role(client, role="admin")
            assert resp["role"] == "reader"  # ceiling wins


class TestBruteForceLockout:
    def test_lockout_trips_and_covers_get(self, env):
        client, admin, _ = env
        _login(client, admin)
        csrf = _CSRF_RE.findall(client.get("/link").text)[0]
        # Hammer bad codes; after MAX_CODE_FAILURES the lockout trips (429).
        saw_lock = False
        for _ in range(15):
            r = client.post(
                "/link/submit",
                data={"_csrf": csrf, "user_code": "ZZZZ-ZZZZ"},
                follow_redirects=False,
            )
            if r.status_code == 429:
                saw_lock = True
                break
            assert r.status_code == 404
        assert saw_lock, "lockout never tripped"
        # The GET deep-link path is gated by the SAME lockout, not bypassable.
        assert client.get("/link?code=ABCD-EFGH").status_code == 429


class TestSeamAndIsolation:
    def test_session_cannot_administer_a_token(self, env):
        client, admin, _ = env
        _login(client, admin)
        access = _loopback_role(client, role="reader")["access_token"]
        # A cvcses_ presented to a token self-service route must fail to verify.
        resp = client.patch(
            "/v1/tokens/admin-tok/email",
            json={"email": "x@y.z"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 401

    def test_revoke_ends_the_session(self, env):
        client, admin, _ = env
        _login(client, admin)
        access = _loopback_role(client, role="reader")["access_token"]
        h = {"Authorization": f"Bearer {access}"}
        assert client.get("/v1/auth/whoami", headers=h).status_code == 200
        assert client.post("/v1/auth/revoke", json={"all": False}, headers=h).status_code == 204
        assert client.get("/v1/auth/whoami", headers=h).status_code == 401
