"""Edge/satellite cluster semantics: the public namespace is upstream-canonical.

A cluster that populates its public catalog from an upstream primary
(``POPULATE_UPSTREAM`` set) treats the public namespace (``org_slug == ""``) as
canonical upstream: local publishes into it are rejected (409); only org-scoped
publishes are accepted, and those stay local.  The chunked upload path is
org-aware too, so large private packages can be published.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi import HTTPException
from fastapi.testclient import TestClient

from cvcpkg.server import app as app_mod
from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

UPSTREAM = "http://upstream.example"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bundles(resp_json: dict) -> list:
    return resp_json.get("packages") or resp_json.get("bundles") or []


@pytest.fixture()
def edge_server(tmp_path, monkeypatch):
    """DB-backed EDGE server (populate upstream set) + publisher token + private 'shell' org."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'edge.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    # Mark this server as an edge cluster (module global read at call time).
    monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", UPSTREAM)

    # Keep the background populate loop out of the endpoint tests.
    async def _noop_loop():
        return

    monkeypatch.setattr(app_mod, "_populate_sync_loop", _noop_loop)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return pub_raw

    pub_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        # The publisher creates a private org and is auto-owner (= member).
        r = client.post(
            "/v1/orgs",
            json={"slug": "shell", "display_name": "Shell", "is_private": True},
            headers=_hdr(pub_token),
        )
        assert r.status_code in (200, 201), r.text
        yield client, pub_token, tmp_path


class TestEdgePublishPolicy:
    def test_public_publish_rejected_on_edge(self, edge_server):
        client, pub_token, _ = edge_server
        r = client.post(
            "/v1/publish",
            params={"name": "libfoo", "version": "1.0.0", "platform": "linux", "arch": "x86_64"},
            files={"file": ("f.tar.zst", b"data", "application/octet-stream")},
            headers=_hdr(pub_token),
        )
        assert r.status_code == 409
        assert "canonical upstream" in r.json()["detail"]

    def test_org_publish_allowed_on_edge(self, edge_server):
        client, pub_token, _ = edge_server
        r = client.post(
            "/v1/publish",
            params={
                "name": "libfoo",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "shell",
            },
            files={"file": ("f.tar.zst", b"data", "application/octet-stream")},
            headers=_hdr(pub_token),
        )
        assert r.status_code in (200, 201), r.text
        got = client.get("/v1/packages", params={"name": "libfoo"}, headers=_hdr(pub_token)).json()
        bundles = _bundles(got)
        assert bundles and all(b.get("org") == "shell" for b in bundles)

    def test_chunked_public_rejected_on_edge(self, edge_server):
        client, pub_token, _ = edge_server
        r = client.post(
            "/v1/upload/init",
            params={"name": "bigfoo", "version": "1.0.0", "platform": "linux", "arch": "x86_64"},
            headers=_hdr(pub_token),
        )
        assert r.status_code == 409
        assert "canonical upstream" in r.json()["detail"]

    def test_chunked_org_upload_publishes_privately(self, edge_server):
        client, pub_token, _ = edge_server
        hdrs = _hdr(pub_token)
        payload = b"Z" * 2048
        sha = hashlib.sha256(payload).hexdigest()

        r = client.post(
            "/v1/upload/init",
            params={
                "name": "bigfoo",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "shell",
            },
            headers=hdrs,
        )
        assert r.status_code == 201, r.text
        upload_id = r.json()["upload_id"]

        r = client.patch(
            f"/v1/upload/{upload_id}",
            content=payload,
            headers={**hdrs, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200

        r = client.post(
            f"/v1/upload/{upload_id}/complete",
            params={"expected_sha256": sha},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text

        got = client.get("/v1/packages", params={"name": "bigfoo"}, headers=hdrs).json()
        bundles = _bundles(got)
        assert bundles and all(b.get("org") == "shell" for b in bundles)

    def test_chunked_unknown_org_rejected(self, edge_server):
        client, pub_token, _ = edge_server
        r = client.post(
            "/v1/upload/init",
            params={
                "name": "bigfoo",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "not-a-real-org",
            },
            headers=_hdr(pub_token),
        )
        assert r.status_code == 404


class TestEdgeRejectHelper:
    def test_helper_rejects_public_on_edge(self, monkeypatch):
        monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", UPSTREAM)
        with pytest.raises(HTTPException) as ei:
            app_mod._reject_public_publish_on_edge("")
        assert ei.value.status_code == 409

    def test_helper_allows_org_on_edge(self, monkeypatch):
        monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", UPSTREAM)
        app_mod._reject_public_publish_on_edge("shell")  # must not raise

    def test_helper_allows_public_off_edge(self, monkeypatch):
        monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", "")
        app_mod._reject_public_publish_on_edge("")  # must not raise


class TestPrivatePackageVisibility:
    """A private org's packages must be invisible to non-members via the
    per-name listing (regression: /v1/packages/{name} leaked them)."""

    def _publish_private(self, client, pub_token):
        r = client.post(
            "/v1/publish",
            params={
                "name": "secretlib",
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "org": "shell",
            },
            files={"file": ("f.tar.zst", b"data", "application/octet-stream")},
            headers=_hdr(pub_token),
        )
        assert r.status_code in (200, 201), r.text

    def test_hidden_from_anonymous_and_bogus_token(self, edge_server):
        client, pub_token, _ = edge_server
        self._publish_private(client, pub_token)
        # Anonymous request must not see the private package.
        anon = client.get("/v1/packages/secretlib", params={"org": "shell"}).json()
        assert _bundles(anon) == []
        # A bogus/invalid token resolves to no identity -> still hidden.
        bogus = client.get(
            "/v1/packages/secretlib",
            params={"org": "shell"},
            headers={"Authorization": "Bearer not-a-real-token"},
        ).json()
        assert _bundles(bogus) == []

    def test_visible_to_member(self, edge_server):
        client, pub_token, _ = edge_server
        self._publish_private(client, pub_token)
        got = client.get(
            "/v1/packages/secretlib", params={"org": "shell"}, headers=_hdr(pub_token)
        ).json()
        bundles = _bundles(got)
        assert bundles and all(b.get("org") == "shell" for b in bundles)
        # The listing now exposes required_deps (needed for federated resolution).
        assert "required_deps" in bundles[0]
