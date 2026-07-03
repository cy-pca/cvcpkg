"""DB-backed tests for ``/v1/search`` — exercises the SQL search + facet paths."""

from __future__ import annotations

import asyncio
import io

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin and publisher tokens."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


def _publish(
    client,
    pub_tok,
    *,
    name,
    version="1.0",
    platform="linux",
    arch="x86_64",
    build_type="release",
    link="shared",
):
    resp = client.post(
        "/v1/publish",
        params={
            "name": name,
            "version": version,
            "platform": platform,
            "arch": arch,
            "build_type": build_type,
            "link": link,
        },
        files={"file": (f"{name}-{version}.tar.zst", io.BytesIO(b"data-" + name.encode()))},
        headers={"Authorization": f"Bearer {pub_tok}"},
    )
    assert resp.status_code == 200, resp.text


def _seed(client, pub_tok):
    _publish(client, pub_tok, name="boost", version="1.86", platform="linux")
    _publish(client, pub_tok, name="boost", version="1.86", platform="darwin", link="static")
    _publish(client, pub_tok, name="fftw3", version="3.3.10", platform="linux", link="static")
    _publish(client, pub_tok, name="fftw3", version="3.3.10", platform="windows")
    _publish(client, pub_tok, name="zlib", version="1.3.1", platform="linux")


class TestSearchEndpointDb:
    def test_empty_query_returns_all(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search").json()
        assert data["total"] == 5
        assert data["package_count"] == 3
        assert data["total_size_bytes"] > 0
        assert len(data["packages"]) == 5

    def test_query_matches_name(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search", params={"q": "boost"}).json()
        assert data["total"] == 2
        assert data["package_count"] == 1
        assert {p["name"] for p in data["packages"]} == {"boost"}

    def test_query_case_insensitive(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        assert client.get("/v1/search", params={"q": "FFTW"}).json()["total"] == 2

    def test_filter_platform(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search", params={"platform": "linux"}).json()
        assert data["total"] == 3
        assert {p["platform"] for p in data["packages"]} == {"linux"}

    def test_query_and_filter_combined(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get(
            "/v1/search", params={"q": "boost", "platform": "darwin"}
        ).json()
        assert data["total"] == 1
        assert data["packages"][0]["name"] == "boost"
        assert data["packages"][0]["platform"] == "darwin"

    def test_pagination(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        page1 = client.get("/v1/search", params={"limit": 2, "offset": 0}).json()
        page2 = client.get("/v1/search", params={"limit": 2, "offset": 2}).json()
        assert page1["total"] == 5 and page2["total"] == 5
        assert len(page1["packages"]) == 2 and len(page2["packages"]) == 2
        ids1 = {(p["name"], p["platform"], p["link"]) for p in page1["packages"]}
        ids2 = {(p["name"], p["platform"], p["link"]) for p in page2["packages"]}
        assert ids1.isdisjoint(ids2)

    def test_facets_populated(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search").json()
        platforms = {b["value"]: b["count"] for b in data["facets"]["platforms"]}
        assert platforms == {"linux": 3, "darwin": 1, "windows": 1}
        links = {b["value"]: b["count"] for b in data["facets"]["links"]}
        assert links == {"shared": 3, "static": 2}

    def test_facets_reflect_filters(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search", params={"platform": "linux"}).json()
        platforms = {b["value"]: b["count"] for b in data["facets"]["platforms"]}
        assert platforms == {"linux": 3}
        # package_count is the distinct name count for the filtered set
        assert data["package_count"] == 3
        assert data["total"] == 3

    def test_facets_off(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search", params={"facets": "false"}).json()
        for key in ("platforms", "archs", "build_types", "links",
                    "releases", "orgs", "tags", "licenses"):
            assert data["facets"][key] == []
        # Still returns totals and packages
        assert data["total"] == 5
        assert len(data["packages"]) == 5

    def test_search_sql_injection_safe(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        resp = client.get(
            "/v1/search", params={"q": "'; DROP TABLE packages; --"}
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        # Table still exists
        assert client.get("/v1/search").json()["total"] == 5

    def test_limit_clamped(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        resp = client.get("/v1/search", params={"limit": 999})
        assert resp.status_code == 422

    def test_no_match(self, db_server_env):
        client, _, pub_tok, _ = db_server_env
        _seed(client, pub_tok)
        data = client.get("/v1/search", params={"q": "no-such-xyz"}).json()
        assert data["total"] == 0
        assert data["package_count"] == 0
        assert data["packages"] == []
