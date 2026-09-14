# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""End-to-end tests for the cvcpkg login broker (server half of Stage 3).

Both grants are exercised against a token->session browser login standing in
for the identity provider (the real tx.wtf leg is exercised only in prod):

* **device pairing** — start, poll (pending), approve in the browser, collect a
  ``cvcses_`` session, and use it as a bearer that resolves to the principal;
* **loopback** — authorize, resume (issues a PKCE-bound code), exchange it, then
  rotate the refresh token and prove reuse detection revokes the family.
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'broker.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.setenv("CVCPKG_COOKIE_SECURE", "0")

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        tokens = DbTokenStore(tmp_path)
        admin = await tokens.create("test-admin", TokenRole.admin)
        await dispose_engine()
        return admin

    admin_tok = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_tok


def _browser_login(client, token):
    resp = client.post("/login", data={"token": token, "next": "/account"})
    assert resp.status_code in (200, 303), resp.text


class TestDevicePairing:
    def test_end_to_end(self, env):
        client, admin = env
        # 1. Device starts a pairing (no auth).
        start = client.post(
            "/v1/auth/device",
            json={
                "client_id": "cvcpkg-cli",
                "device_label": "catx-03",
                "platform": "linux-x86_64",
                "client_version": "2.1.0",
                "verifier_hash": hashlib.sha256(b"the-verifier").hexdigest(),
                "requested_role": "publisher",
            },
        )
        assert start.status_code == 200, start.text
        pairing_id = start.json()["pairing_id"]
        user_code = start.json()["user_code"]

        # 2. Polling before approval is authorization_pending.
        poll = client.post(
            "/v1/auth/device/token", json={"pairing_id": pairing_id, "verifier": "the-verifier"}
        )
        assert poll.status_code == 400 and poll.json()["error"] == "authorization_pending"

        # 3. The human signs in and approves in the browser.
        _browser_login(client, admin)
        page = client.get(f"/link?code={user_code}")
        assert page.status_code == 200
        assert "Approve this device?" in page.text
        assert "catx-03" in page.text
        csrf = _CSRF_RE.findall(page.text)[0]
        approve = client.post(
            "/link/approve",
            data={"_csrf": csrf, "user_code": user_code, "role": "publisher"},
        )
        assert approve.status_code == 200 and "approved" in approve.text.lower()

        # 4. The device collects its session.
        poll2 = client.post(
            "/v1/auth/device/token", json={"pairing_id": pairing_id, "verifier": "the-verifier"}
        )
        assert poll2.status_code == 200, poll2.text
        access = poll2.json()["access_token"]
        assert access.startswith("cvcses_")
        assert poll2.json()["principal"] == "test-admin"
        assert poll2.json()["role"] == "publisher"

        # 5. Collecting twice yields nothing (minted once).
        poll3 = client.post(
            "/v1/auth/device/token", json={"pairing_id": pairing_id, "verifier": "the-verifier"}
        )
        assert poll3.status_code == 400

        # 6. The session works as a bearer and resolves to the principal.
        who = client.get("/v1/auth/whoami", headers={"Authorization": f"Bearer {access}"})
        assert who.status_code == 200, who.text
        assert who.json()["name"] == "test-admin"
        assert who.json()["kind"] == "session"

    def test_wrong_verifier_is_refused(self, env):
        client, admin = env
        start = client.post(
            "/v1/auth/device",
            json={"client_id": "cvcpkg-cli", "verifier_hash": hashlib.sha256(b"real").hexdigest()},
        )
        pairing_id = start.json()["pairing_id"]
        poll = client.post(
            "/v1/auth/device/token", json={"pairing_id": pairing_id, "verifier": "wrong"}
        )
        assert poll.status_code == 400 and poll.json()["error"] == "access_denied"


class TestLoopbackAndRefresh:
    def _get_code(self, client, challenge, redirect):
        # authorize parks a txn and redirects to the sign-in leg.
        auth = client.get(
            "/v1/auth/authorize",
            params={
                "client_id": "cvcpkg-cli",
                "redirect_uri": redirect,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "cli-state-xyz",
            },
            follow_redirects=False,
        )
        assert auth.status_code == 303, auth.text
        loc = auth.headers["location"]
        txn = loc.split("/v1/auth/resume/")[1]
        # With a live browser session, resume issues the code + bounces to loopback.
        resume = client.get(f"/v1/auth/resume/{txn}", follow_redirects=False)
        assert resume.status_code == 302, resume.text
        target = resume.headers["location"]
        assert target.startswith(redirect)
        assert "state=cli-state-xyz" in target
        return re.search(r"[?&]code=([^&]+)", target).group(1)

    def test_authorization_code_then_refresh_rotation(self, env):
        client, admin = env
        _browser_login(client, admin)
        verifier, challenge = _pkce()
        redirect = "http://127.0.0.1:49812/callback"
        code = self._get_code(client, challenge, redirect)

        # Exchange the code (PKCE) for a session.
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
        access = tok.json()["access_token"]
        refresh = tok.json()["refresh_token"]
        assert access.startswith("cvcses_") and refresh.startswith("cvcref_")

        # A replayed code is refused (single-use burn).
        replay = client.post(
            "/v1/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": "cvcpkg-cli",
                "redirect_uri": redirect,
            },
        )
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"

        # Rotate the refresh token.
        r1 = client.post(
            "/v1/auth/token", data={"grant_type": "refresh_token", "refresh_token": refresh}
        )
        assert r1.status_code == 200, r1.text
        new_refresh = r1.json()["refresh_token"]
        assert new_refresh != refresh

        # Reusing the burned refresh revokes the whole family.
        reuse = client.post(
            "/v1/auth/token", data={"grant_type": "refresh_token", "refresh_token": refresh}
        )
        assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
        # ...and the successor is now dead too.
        after = client.post(
            "/v1/auth/token", data={"grant_type": "refresh_token", "refresh_token": new_refresh}
        )
        assert after.status_code == 400

    def test_bad_redirect_uri_refused(self, env):
        client, admin = env
        _browser_login(client, admin)
        _, challenge = _pkce()
        auth = client.get(
            "/v1/auth/authorize",
            params={
                "client_id": "cvcpkg-cli",
                "redirect_uri": "http://localhost:49812/callback",  # localhost, not a literal IP
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "x",
            },
            follow_redirects=False,
        )
        assert auth.status_code == 400
