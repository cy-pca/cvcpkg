"""Extra endpoint coverage for ``cvcpkg.server.app``.

These tests drive the FastAPI app through the same in-memory (no-database)
``TestClient`` harness ``test_server.py`` uses, but focus on the many routes and
error branches that file does not reach: the static/HTML pages, the recipe and
dependency-graph readers, the chunked-upload lifecycle, token self-service,
user search, registration, the RSS feed, and the auth/role/DB-gating error
branches (401/403/404/409/422/501/503) across the admin, analytics, org, tag,
mirror, builder, build and CLI-auth families.

The app runs without ``CVCPKG_DATABASE_URL`` here, so every database-backed
route takes its "requires a database backend" branch — which is itself a real,
asserted behaviour.
"""

from __future__ import annotations

import io

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pydantic = pytest.importorskip("pydantic", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import TokenRole


# ── Fixtures / helpers ──────────────────────────────────────────


@pytest.fixture()
def env(tmp_path):
    """A TestClient plus admin / publisher / reader bearer tokens.

    Tokens are minted into the on-disk TokenStore *before* the app starts so
    the lifespan loads them, mirroring ``test_server.server_env``.
    """
    store = TokenStore(tmp_path)
    admin = store.create("admin-user", TokenRole.admin)
    publisher = store.create("pub-user", TokenRole.publisher)
    reader = store.create("read-user", TokenRole.reader)
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield {
            "client": client,
            "admin": admin,
            "publisher": publisher,
            "reader": reader,
            "tmp_path": tmp_path,
        }


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _publish(
    client,
    tok,
    *,
    name,
    version="1.0",
    platform="linux",
    arch="x86_64",
    build_type="release",
    link="shared",
    content=None,
    **params,
):
    body = content if content is not None else b"archive-" + name.encode()
    q = {
        "name": name,
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": build_type,
        "link": link,
    }
    q.update(params)
    return client.post(
        "/v1/publish",
        params=q,
        files={"file": (f"{name}-{version}.tar.zst", io.BytesIO(body))},
        headers=_auth(tok),
    )


# ── Static / HTML pages ─────────────────────────────────────────


class TestStaticAndHtmlPages:
    def test_landing_search_guide(self, env):
        c = env["client"]
        for path in ("/", "/search", "/guide", "/orgs", "/org/some-org"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert "text/html" in r.headers["content-type"]

    def test_package_detail_page(self, env):
        r = env["client"].get("/package/zlib")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_browse_pages(self, env):
        c = env["client"]
        for path in ("/tags", "/tag/utils", "/builders", "/builds", "/recipes", "/build/1"):
            r = c.get(path)
            assert r.status_code == 200, path

    def test_install_scripts(self, env):
        c = env["client"]
        sh = c.get("/install.sh")
        assert sh.status_code == 200
        assert "shellscript" in sh.headers["content-type"]
        ps1 = c.get("/install.ps1")
        assert ps1.status_code == 200
        assert ps1.content  # non-empty installer body

    def test_brand_assets(self, env):
        c = env["client"]
        # Unknown asset name → 404 (allow-list guard), not an arbitrary read.
        assert c.get("/assets/definitely-not-an-asset.png").status_code == 404
        # The self-hosted brand logo is always available.
        logo = c.get("/favicon.ico")
        assert logo.status_code == 200
        assert logo.headers["content-type"]
        assert c.get("/assets/cyberpc-angel-gears.png").status_code == 200

    def test_login_and_account_require_db(self, env):
        c = env["client"]
        # Account/login surfaces need the DB-backed principal store.
        assert c.get("/login", follow_redirects=False).status_code == 501
        assert c.get("/account", follow_redirects=False).status_code == 501


# ── Health / metrics / catalog ──────────────────────────────────


class TestHealthMetricsCatalog:
    def test_healthz_shape(self, env):
        data = env["client"].get("/healthz").json()
        assert data["status"] == "ok"
        assert data["version"]
        assert data["packages_count"] == 0
        assert data["mirror_mode"] is False
        assert data["storage_scheme"] == "file"

    def test_metrics_text(self, env):
        r = env["client"].get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "cvcpkg_up 1" in r.text
        assert "cvcpkg_requests_total" in r.text
        assert 'cvcpkg_requests_by_method{method="GET"}' in r.text

    def test_head_catalog(self, env):
        assert env["client"].head("/v1/catalog").status_code == 200

    def test_catalog_resolves_absolute_urls(self, env):
        c, pub = env["client"], env["publisher"]
        assert _publish(c, pub, name="zlib", version="1.3.1").status_code == 200
        cat = c.get("/v1/catalog").json()
        assert cat["revision"] >= 0  # local backend maintains a monotonic revision
        assert len(cat["bundles"]) == 1
        # Relative /v1/download/... is rewritten to an absolute URL.
        assert cat["bundles"][0]["archive_url"].startswith("http")
        assert "/v1/download/" in cat["bundles"][0]["archive_url"]


# ── Packages / get_package ──────────────────────────────────────


class TestPackagesListing:
    def test_filters_and_pagination(self, env):
        c, pub = env["client"], env["publisher"]
        _publish(c, pub, name="zlib", version="1.0", platform="linux", link="shared")
        _publish(c, pub, name="zlib", version="1.0", platform="linux", link="static")
        _publish(c, pub, name="boost", version="1.8", platform="macos", arch="arm64")

        assert c.get("/v1/packages").json()["total"] == 3
        assert c.get("/v1/packages", params={"name": "zlib"}).json()["total"] == 2
        assert c.get("/v1/packages", params={"platform": "macos"}).json()["total"] == 1
        assert c.get("/v1/packages", params={"arch": "arm64"}).json()["total"] == 1
        assert c.get("/v1/packages", params={"link": "static"}).json()["total"] == 1
        assert c.get("/v1/packages", params={"build_type": "release"}).json()["total"] == 3
        assert c.get("/v1/packages", params={"search": "boost"}).json()["total"] == 1
        # Pagination: limit trims the page but total stays full.
        page = c.get("/v1/packages", params={"limit": 1, "offset": 0}).json()
        assert page["total"] == 3 and len(page["packages"]) == 1

    def test_release_live_filter(self, env):
        c, pub = env["client"], env["publisher"]
        _publish(c, pub, name="live-pkg", version="1.0")  # no release tag
        _publish(c, pub, name="tagged-pkg", version="2.0", release_tag="v2.0")
        live = c.get("/v1/packages", params={"release": "live"}).json()
        assert {p["name"] for p in live["packages"]} == {"live-pkg"}
        tagged = c.get("/v1/packages", params={"release": "v2.0"}).json()
        assert {p["name"] for p in tagged["packages"]} == {"tagged-pkg"}

    def test_get_package_and_yanked_visibility(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        _publish(c, pub, name="curl", version="8.0")
        assert c.get("/v1/packages/curl").json()["total"] == 1
        assert c.get("/v1/packages/missing").json()["total"] == 0
        # Yank it, then it disappears from the default view but not include_yanked.
        assert c.post("/v1/packages/curl/8.0/yank", headers=_auth(admin)).status_code == 200
        assert c.get("/v1/packages/curl").json()["total"] == 0
        assert c.get("/v1/packages/curl", params={"include_yanked": True}).json()["total"] == 1


# ── Search ──────────────────────────────────────────────────────


class TestSearchExtra:
    def test_query_filters_and_facets(self, env):
        c, pub = env["client"], env["publisher"]
        _publish(c, pub, name="alpha", version="1.0", platform="linux", link="shared")
        _publish(c, pub, name="alpha", version="1.0", platform="windows", link="static")
        _publish(c, pub, name="beta", version="2.0", platform="linux")

        r = c.get("/v1/search", params={"q": "alpha"})
        assert r.status_code == 200
        assert r.json()["total"] == 2

        r2 = c.get("/v1/search", params={"platform": "linux", "include_facets": True})
        body = r2.json()
        assert r2.status_code == 200
        assert body["total"] == 2
        assert "facets" in body

        # limit=0 is a valid "count only" request.
        assert c.get("/v1/search", params={"q": "beta", "limit": 0}).json()["total"] == 1


# ── Recipe readers ──────────────────────────────────────────────


class TestRecipeEndpoints:
    def test_recipe_yaml(self, env):
        r = env["client"].get("/v1/recipe/zlib")
        assert r.status_code == 200
        assert "text/yaml" in r.headers["content-type"]
        assert "name: zlib" in r.text or "name:" in r.text

    def test_recipe_files_listing(self, env):
        r = env["client"].get("/v1/recipe/zlib/files")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "zlib"
        paths = {e["path"] for e in body["files"]}
        assert "zlib/recipe.yaml" in paths
        kinds = {e["kind"] for e in body["files"]}
        assert "recipe" in kinds  # the recipe.yaml itself
        assert any(e["kind"] == "script" for e in body["files"])  # build.sh etc.

    def test_recipe_single_file(self, env):
        r = env["client"].get("/v1/recipe/zlib/file", params={"path": "zlib/recipe.yaml"})
        assert r.status_code == 200
        assert "schema_version" in r.text
        # Missing file inside a real recipe → 404.
        miss = env["client"].get("/v1/recipe/zlib/file", params={"path": "zlib/nope.xyz"})
        assert miss.status_code == 404

    def test_recipe_archive_targz_and_zip(self, env):
        c = env["client"]
        tgz = c.get("/v1/recipe/zlib/archive")
        assert tgz.status_code == 200
        assert tgz.headers["content-type"] == "application/gzip"
        assert "attachment" in tgz.headers["content-disposition"]
        assert len(tgz.content) > 0
        z = c.get("/v1/recipe/zlib/archive", params={"format": "zip"})
        assert z.status_code == 200
        assert z.headers["content-type"] == "application/zip"

    def test_recipe_error_branches(self, env):
        c = env["client"]
        # Name that fails the strict recipe-name regex → 400.
        assert c.get("/v1/recipe/_leading-underscore").status_code == 400
        # Invalid org slug on an otherwise valid name → 400.
        assert c.get("/v1/recipe/zlib", params={"org": "_bad"}).status_code == 400
        # Unknown recipe → 404.
        assert c.get("/v1/recipe/no-such-recipe-xyz").status_code == 404
        # A bad archive format is rejected by the query pattern → 422.
        assert c.get("/v1/recipe/zlib/archive", params={"format": "rar"}).status_code == 422


class TestDependencyGraph:
    def test_deps_graph_shape(self, env):
        r = env["client"].get("/v1/deps")
        assert r.status_code == 200
        body = r.json()
        for key in ("forward", "reverse", "meta", "recipe_names"):
            assert key in body
        # The vendored recipe set is non-empty, so the graph has entries.
        assert body["recipe_names"]
        assert isinstance(body["forward"], dict)


# ── Cache family ────────────────────────────────────────────────


class TestCacheEndpoints:
    def test_cache_status_hit_and_miss(self, env):
        c, pub = env["client"], env["publisher"]
        _publish(c, pub, name="zstd", version="1.5", platform="linux", recipe_version="deadbeef")
        hit = c.get(
            "/v1/cache/status",
            params={"name": "zstd", "chain_hash": "deadbeef", "platform": "linux"},
        )
        assert hit.status_code == 200 and hit.json()["hit"] is True
        miss = c.get(
            "/v1/cache/status", params={"name": "zstd", "chain_hash": "other", "platform": "linux"}
        )
        assert miss.status_code == 200 and miss.json()["hit"] is False

    def test_cache_status_requires_query(self, env):
        # Missing required params → 422.
        assert env["client"].get("/v1/cache/status").status_code == 422

    def test_cache_list_and_stats_auth(self, env):
        c, pub, reader = env["client"], env["publisher"], env["reader"]
        _publish(c, pub, name="lz4", version="1.9")
        # Publisher may list cache entries and read stats.
        assert c.get("/v1/cache", headers=_auth(pub)).status_code == 200
        stats = c.get("/v1/cache/stats", headers=_auth(pub))
        assert stats.status_code == 200
        assert stats.json()["total_packages"] == 1
        # Reader is forbidden; no auth is unauthorized.
        assert c.get("/v1/cache", headers=_auth(reader)).status_code == 403
        assert c.get("/v1/cache").status_code == 401

    def test_cache_delete_and_gc_need_db(self, env):
        c, admin = env["client"], env["admin"]
        assert c.delete("/v1/cache", headers=_auth(admin)).status_code == 501
        assert (
            c.post("/v1/cache/gc", json={"max_age_seconds": 5}, headers=_auth(admin)).status_code
            == 501
        )
        # Auth still enforced ahead of the DB check.
        assert c.delete("/v1/cache").status_code == 401
        assert c.post("/v1/cache/gc", json={}, headers=_auth(env["reader"])).status_code == 403


# ── Download ────────────────────────────────────────────────────


class TestDownload:
    def test_head_and_get_roundtrip(self, env):
        c, pub = env["client"], env["publisher"]
        content = b"zip-bytes" * 50
        _publish(c, pub, name="pngpkg", version="1.6", content=content)
        url = c.get("/v1/catalog").json()["bundles"][0]["archive_url"]
        # HEAD reports a size, GET returns the exact bytes.
        h = c.head(url)
        assert h.status_code == 200
        assert int(h.headers["Content-Length"]) == len(content)
        g = c.get(url)
        assert g.status_code == 200 and g.content == content

    def test_missing_and_traversal(self, env):
        c = env["client"]
        assert c.head("/v1/download/nope.tar.zst").status_code == 404
        assert c.get("/v1/download/nope.tar.zst").status_code == 404
        # A traversal attempt collapses to a basename and 404s (never escapes).
        assert c.get("/v1/download/..%2f..%2fetc%2fpasswd").status_code == 404


# ── Publish error branches ──────────────────────────────────────


class TestPublishBranches:
    def test_auth_branches(self, env):
        c = env["client"]
        assert _publish(c, "", name="x").status_code == 401  # no bearer
        assert _publish(c, env["reader"], name="x").status_code == 403  # reader forbidden

    def test_noncanonical_platform_and_arch(self, env):
        c, pub = env["client"], env["publisher"]
        assert _publish(c, pub, name="p", platform="linuxx").status_code == 422
        # aarch64 is a known alias → 422 with a "did you mean" hint.
        r = _publish(c, pub, name="p", arch="aarch64")
        assert r.status_code == 422
        assert "arm64" in r.json()["detail"]

    def test_bad_org_slug(self, env):
        c, pub = env["client"], env["publisher"]
        assert _publish(c, pub, name="p", org="Bad--Slug").status_code == 422

    def test_duplicate_conflict(self, env):
        c, pub = env["client"], env["publisher"]
        assert _publish(c, pub, name="dup", version="1.0").status_code == 200
        r = _publish(c, pub, name="dup", version="1.0")
        assert r.status_code == 409

    def test_publish_with_org_and_tags(self, env):
        c, pub = env["client"], env["publisher"]
        r = _publish(
            c,
            pub,
            name="scoped",
            version="1.0",
            org="cvc-lab",
            tags="utils,net",
            description="d",
            homepage="h",
            maintainer="m",
        )
        assert r.status_code == 200
        assert r.json()["name"] == "scoped"
        # It is discoverable filtered by org.
        assert c.get("/v1/packages", params={"org": "cvc-lab"}).json()["total"] == 1


# ── Chunked upload lifecycle ────────────────────────────────────


class TestChunkedUpload:
    def _init(self, c, tok, **kw):
        params = {
            "name": kw.pop("name", "chunky"),
            "version": kw.pop("version", "1.0"),
            "platform": "linux",
            "arch": "x86_64",
        }
        params.update(kw)
        return c.post("/v1/upload/init", params=params, headers=_auth(tok))

    def test_full_flow(self, env):
        c, pub = env["client"], env["publisher"]
        import hashlib

        payload = b"chunked-archive-data" * 20
        init = self._init(c, pub)
        assert init.status_code == 201
        uid = init.json()["upload_id"]

        # Status before any chunk.
        st = c.get(f"/v1/upload/{uid}", headers=_auth(pub))
        assert st.status_code == 200 and st.json()["bytes_received"] == 0

        # Send the body as one ranged chunk.
        patch = c.patch(
            f"/v1/upload/{uid}",
            content=payload,
            headers={**_auth(pub), "Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
        )
        assert patch.status_code == 200
        assert patch.json()["bytes_received"] == len(payload)

        # Complete with the correct sha256, then the archive is downloadable.
        digest = hashlib.sha256(payload).hexdigest()
        done = c.post(
            f"/v1/upload/{uid}/complete", params={"expected_sha256": digest}, headers=_auth(pub)
        )
        assert done.status_code == 200, done.text
        assert done.json()["sha256"] == digest
        assert c.get(done.json()["archive_url"]).content == payload

    def test_init_noncanonical_platform(self, env):
        c, pub = env["client"], env["publisher"]
        assert self._init(c, pub, platform="notreal").status_code == 422

    def test_missing_session_and_wrong_actor(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        assert c.get("/v1/upload/nope", headers=_auth(pub)).status_code == 404
        assert c.patch("/v1/upload/nope", content=b"x", headers=_auth(pub)).status_code == 404
        assert c.post("/v1/upload/nope/complete", headers=_auth(pub)).status_code == 404
        assert c.delete("/v1/upload/nope", headers=_auth(pub)).status_code == 404

        # A session started by the publisher cannot be driven by another actor.
        uid = self._init(c, pub, name="owned").json()["upload_id"]
        assert c.patch(f"/v1/upload/{uid}", content=b"x", headers=_auth(admin)).status_code == 403
        assert c.post(f"/v1/upload/{uid}/complete", headers=_auth(admin)).status_code == 403
        assert c.delete(f"/v1/upload/{uid}", headers=_auth(admin)).status_code == 403

    def test_complete_no_data_and_sha_mismatch(self, env):
        c, pub = env["client"], env["publisher"]
        uid = self._init(c, pub, name="empty").json()["upload_id"]
        # No bytes uploaded yet → 400.
        assert c.post(f"/v1/upload/{uid}/complete", headers=_auth(pub)).status_code == 400
        # Upload a byte, then a wrong expected digest → 422 and the session is gone.
        c.patch(f"/v1/upload/{uid}", content=b"z", headers=_auth(pub))
        bad = c.post(
            f"/v1/upload/{uid}/complete", params={"expected_sha256": "0" * 64}, headers=_auth(pub)
        )
        assert bad.status_code == 422
        assert c.get(f"/v1/upload/{uid}", headers=_auth(pub)).status_code == 404

    def test_malformed_and_mismatched_range(self, env):
        c, pub = env["client"], env["publisher"]
        uid = self._init(c, pub, name="ranged").json()["upload_id"]
        bad = c.patch(
            f"/v1/upload/{uid}", content=b"abc", headers={**_auth(pub), "Content-Range": "garbage"}
        )
        assert bad.status_code == 400
        off = c.patch(
            f"/v1/upload/{uid}",
            content=b"abc",
            headers={**_auth(pub), "Content-Range": "bytes 99-101/200"},
        )
        assert off.status_code == 409

    def test_cancel(self, env):
        c, pub = env["client"], env["publisher"]
        uid = self._init(c, pub, name="cancelme").json()["upload_id"]
        assert c.delete(f"/v1/upload/{uid}", headers=_auth(pub)).status_code == 204
        assert c.get(f"/v1/upload/{uid}", headers=_auth(pub)).status_code == 404


# ── Yank / unyank / delete / nuke / tombstones ──────────────────


class TestLifecycleMutations:
    def test_yank_scope_and_unyank(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        _publish(c, pub, name="scoped", version="1.0", platform="linux", link="shared")
        _publish(c, pub, name="scoped", version="1.0", platform="linux", link="static")
        # Yank only the static variant.
        y = c.post("/v1/packages/scoped/1.0/yank", params={"link": "static"}, headers=_auth(admin))
        assert y.status_code == 200 and y.json()["count"] == 1
        assert c.get("/v1/packages/scoped").json()["total"] == 1
        # Unyank (admin only) restores it.
        u = c.post(
            "/v1/packages/scoped/1.0/unyank", params={"link": "static"}, headers=_auth(admin)
        )
        assert u.status_code == 200 and u.json()["count"] == 1
        assert c.get("/v1/packages/scoped").json()["total"] == 2

    def test_yank_auth(self, env):
        c = env["client"]
        assert c.post("/v1/packages/x/1/yank").status_code == 401
        assert c.post("/v1/packages/x/1/yank", headers=_auth(env["reader"])).status_code == 403
        # unyank is admin-only: publisher is forbidden.
        assert c.post("/v1/packages/x/1/unyank", headers=_auth(env["publisher"])).status_code == 403

    def test_delete_and_by_link(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        _publish(c, pub, name="delme", version="1.0", platform="linux", link="shared")
        d = c.delete("/v1/packages/delme/1.0", headers=_auth(admin))
        assert d.status_code == 200 and d.json()["removed"] == 1
        # Deleting again → 404.
        assert c.delete("/v1/packages/delme/1.0", headers=_auth(admin)).status_code == 404

        _publish(c, pub, name="linkpkg", version="2.0", platform="windows", link="static")
        bl = c.delete("/v1/packages/by-link/windows/static", headers=_auth(admin))
        assert bl.status_code == 200 and bl.json()["removed"] == 1
        assert (
            c.delete("/v1/packages/by-link/windows/static", headers=_auth(admin)).status_code == 404
        )

    def test_nuke_needs_db_and_tombstones_empty(self, env):
        c, admin = env["client"], env["admin"]
        assert c.post("/v1/packages/x/1/nuke", headers=_auth(admin)).status_code == 501
        # Tombstones simply come back empty on the local backend.
        tb = c.get("/v1/packages/x/tombstones")
        assert tb.status_code == 200 and tb.json() == {"tombstones": [], "count": 0}
        # Auth branches.
        assert c.post("/v1/packages/x/1/nuke").status_code == 401
        assert c.post("/v1/packages/x/1/nuke", headers=_auth(env["publisher"])).status_code == 403


# ── Token management + self-service ─────────────────────────────


class TestTokenManagement:
    def test_create_list_revoke(self, env):
        c, admin = env["client"], env["admin"]
        created = c.post(
            "/v1/tokens", json={"name": "ci_bot", "role": "publisher"}, headers=_auth(admin)
        )
        assert created.status_code == 200
        assert created.json()["token"].startswith("cvctok_")
        names = {t["name"] for t in c.get("/v1/tokens", headers=_auth(admin)).json()["tokens"]}
        assert {"admin-user", "pub-user", "read-user", "ci_bot"} <= names
        assert c.delete("/v1/tokens/ci_bot", headers=_auth(admin)).status_code == 200
        assert c.delete("/v1/tokens/ci_bot", headers=_auth(admin)).status_code == 404

    def test_create_validation(self, env):
        c, admin = env["client"], env["admin"]
        assert (
            c.post("/v1/tokens", json={"name": "bad name!"}, headers=_auth(admin)).status_code
            == 422
        )
        assert (
            c.post("/v1/tokens", json={"name": "admin-user"}, headers=_auth(admin)).status_code
            == 409
        )

    def test_create_auth(self, env):
        c = env["client"]
        assert c.post("/v1/tokens", json={"name": "n"}).status_code == 401
        assert (
            c.post("/v1/tokens", json={"name": "n"}, headers=_auth(env["publisher"])).status_code
            == 403
        )

    def test_email_update_paths(self, env):
        c, admin, reader = env["client"], env["admin"], env["reader"]
        # No header / bad token.
        assert c.patch("/v1/tokens/read-user/email", json={"email": "a@b.c"}).status_code == 401
        assert (
            c.patch(
                "/v1/tokens/read-user/email", json={"email": "a@b.c"}, headers=_auth("cvctok_bogus")
            ).status_code
            == 401
        )
        # A reader updating its own email succeeds.
        assert (
            c.patch(
                "/v1/tokens/read-user/email", json={"email": "me@x.io"}, headers=_auth(reader)
            ).status_code
            == 200
        )
        # A reader cannot touch someone else's token.
        assert (
            c.patch(
                "/v1/tokens/admin-user/email", json={"email": "x@y.z"}, headers=_auth(reader)
            ).status_code
            == 403
        )
        # Admin can set any token's email; unknown token → 404.
        assert (
            c.patch(
                "/v1/tokens/pub-user/email", json={"email": "p@x.io"}, headers=_auth(admin)
            ).status_code
            == 200
        )
        assert (
            c.patch(
                "/v1/tokens/ghost/email", json={"email": "g@x.io"}, headers=_auth(admin)
            ).status_code
            == 404
        )

    def test_profile_update_paths(self, env):
        c, admin, reader = env["client"], env["admin"], env["reader"]
        assert (
            c.patch("/v1/tokens/read-user/profile", json={"description": "hi"}).status_code == 401
        )
        assert (
            c.patch(
                "/v1/tokens/read-user/profile", json={"description": "self"}, headers=_auth(reader)
            ).status_code
            == 200
        )
        assert (
            c.patch(
                "/v1/tokens/admin-user/profile", json={"description": "no"}, headers=_auth(reader)
            ).status_code
            == 403
        )
        assert (
            c.patch(
                "/v1/tokens/ghost/profile", json={"metadata": "{}"}, headers=_auth(admin)
            ).status_code
            == 404
        )

    def test_rotate_paths(self, env):
        c, admin, reader = env["client"], env["admin"], env["reader"]
        assert c.post("/v1/tokens/read-user/rotate").status_code == 401
        # Reader cannot rotate another token; unknown token → 404 (admin).
        # (Checked before rotating the reader's own secret, which would
        # invalidate the very token these calls authenticate with.)
        assert c.post("/v1/tokens/admin-user/rotate", headers=_auth(reader)).status_code == 403
        assert c.post("/v1/tokens/ghost/rotate", headers=_auth(admin)).status_code == 404
        # Reader rotates its own secret (grace 0 → old secret dies immediately).
        rot = c.post(
            "/v1/tokens/read-user/rotate", json={"grace_minutes": 0}, headers=_auth(reader)
        )
        assert rot.status_code == 200
        assert rot.json()["token"].startswith("cvctok_")


# ── Users ───────────────────────────────────────────────────────


class TestUsers:
    def test_list_users_and_sorting(self, env):
        c = env["client"]
        r = c.get("/v1/users")
        assert r.status_code == 200
        names = {u["name"] for u in r.json()["users"]}
        assert {"admin-user", "pub-user", "read-user"} <= names
        # Role filter and desc sort.
        admins = c.get("/v1/users", params={"role": "admin"}).json()
        assert all(u["role"] == "admin" for u in admins["users"])
        desc = c.get("/v1/users", params={"sort": "name", "order": "desc"}).json()
        sorted_names = [u["name"] for u in desc["users"]]
        assert sorted_names == sorted(sorted_names, reverse=True)

    def test_list_users_validation(self, env):
        c = env["client"]
        assert c.get("/v1/users", params={"sort": "bogus"}).status_code == 422
        assert c.get("/v1/users", params={"order": "sideways"}).status_code == 422

    def test_by_name_and_by_email(self, env):
        c, admin = env["client"], env["admin"]
        # Give a token an email so the by-email lookup can find it.
        c.patch("/v1/tokens/admin-user/email", json={"email": "boss@corp.io"}, headers=_auth(admin))
        assert c.get("/v1/users/admin-user").json()["name"] == "admin-user"
        assert c.get("/v1/users/nobody-here").status_code == 404
        found = c.get("/v1/users/by-email/boss@corp.io")
        assert found.status_code == 200 and found.json()["name"] == "admin-user"
        assert c.get("/v1/users/by-email/no-one@nowhere.io").status_code == 404


# ── Registration (open mode) ────────────────────────────────────


class TestRegistration:
    def test_open_registration(self, env):
        c = env["client"]
        r = c.post("/v1/register", json={"name": "newbie", "email": "n@x.io"})
        assert r.status_code == 200
        assert r.json()["token"].startswith("cvctok_")
        # The new self-registered token can authenticate as a reader.
        assert c.get("/v1/tokens", headers=_auth(r.json()["token"])).status_code == 403

    def test_validation_branches(self, env):
        c = env["client"]
        assert c.post("/v1/register", json={"name": "", "email": "a@b.c"}).status_code == 422
        assert (
            c.post("/v1/register", json={"name": "bad name", "email": "a@b.c"}).status_code == 422
        )
        assert c.post("/v1/register", json={"name": "ok", "email": ""}).status_code == 422

    def test_duplicate_registration(self, env):
        c = env["client"]
        c.post("/v1/register", json={"name": "twin", "email": "t@x.io"})
        dup = c.post("/v1/register", json={"name": "twin", "email": "t2@x.io"})
        assert dup.status_code == 409

    def test_token_requests_need_db(self, env):
        c, admin = env["client"], env["admin"]
        assert c.get("/v1/token-requests", headers=_auth(admin)).status_code == 501
        assert c.post("/v1/token-requests/1/approve", headers=_auth(admin)).status_code == 501
        assert c.post("/v1/token-requests/1/deny", headers=_auth(admin)).status_code == 501
        assert c.get("/v1/token-requests").status_code == 401


# ── Audit ───────────────────────────────────────────────────────


class TestAudit:
    def test_audit_log_and_verify(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        _publish(c, pub, name="audited", version="1.0")  # generates a publish audit entry
        log = c.get("/v1/audit", headers=_auth(admin))
        assert log.status_code == 200
        assert log.json()["total"] >= 1
        # Filter by action.
        filtered = c.get("/v1/audit", params={"action": "publish"}, headers=_auth(admin))
        assert filtered.status_code == 200
        v = c.get("/v1/audit/verify", headers=_auth(admin))
        assert v.status_code == 200 and v.json()["ok"] is True

    def test_audit_auth(self, env):
        c = env["client"]
        assert c.get("/v1/audit").status_code == 401
        assert c.get("/v1/audit", headers=_auth(env["publisher"])).status_code == 403
        assert c.get("/v1/audit/verify", headers=_auth(env["reader"])).status_code == 403


# ── RSS feed / download stats ───────────────────────────────────


class TestFeedAndDownloadStats:
    def test_rss_feed(self, env):
        c, pub = env["client"], env["publisher"]
        _publish(
            c, pub, name="feedpkg", version="3.0", platform="linux", description="a feed package"
        )
        r = c.get("/v1/feed.xml")
        assert r.status_code == 200
        assert "application/rss+xml" in r.headers["content-type"]
        assert "<rss" in r.text
        assert "feedpkg" in r.text

    def test_download_stats_local_default(self, env):
        r = env["client"].get("/v1/downloads/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["daily"] == []
        assert "config" in body


# ── Analytics / telemetry (DB-gated) ────────────────────────────


class TestAnalyticsGated:
    def test_analytics_require_db(self, env):
        c, admin = env["client"], env["admin"]
        for path in (
            "/v1/analytics/downloads",
            "/v1/analytics/bandwidth",
            "/v1/analytics/platforms",
            "/v1/analytics/trends",
            "/v1/analytics/telemetry",
        ):
            assert c.get(path, headers=_auth(admin)).status_code == 503, path

    def test_analytics_auth(self, env):
        c = env["client"]
        assert c.get("/v1/analytics/downloads").status_code == 401
        assert c.get("/v1/analytics/trends", headers=_auth(env["reader"])).status_code == 403

    def test_telemetry(self, env):
        c = env["client"]
        # Local backend has no telemetry store → 503 (the DB gate fires before
        # the payload-shape checks).
        assert c.post("/v1/telemetry", json={"platform": "linux"}).status_code == 503
        payload = {"platform": "linux", "tools": {f"t{i}": "1" for i in range(17)}}
        assert c.post("/v1/telemetry", json=payload).status_code == 503


# ── Admin surface ───────────────────────────────────────────────


class TestAdminSurface:
    def test_shutdown_auth_and_success(self, env):
        c, admin = env["client"], env["admin"]
        assert c.post("/v1/admin/shutdown").status_code == 401
        assert c.post("/v1/admin/shutdown", headers=_auth(env["reader"])).status_code == 403
        from unittest.mock import patch as _patch

        with _patch("os.kill"):
            ok = c.post("/v1/admin/shutdown", headers=_auth(admin))
        assert ok.status_code == 200 and "shutting down" in ok.json()["message"]

    def test_update_builders_and_stats(self, env):
        c, admin = env["client"], env["admin"]
        ub = c.post("/v1/admin/update-builders", headers=_auth(admin))
        assert ub.status_code == 200 and ub.json()["total_connected"] == 0
        stats = c.get("/v1/admin/stats", headers=_auth(admin))
        assert stats.status_code == 200
        assert stats.json()["database_enabled"] is False
        assert stats.json()["packages_count"] == 0
        assert c.get("/v1/admin/stats", headers=_auth(env["publisher"])).status_code == 403

    def test_backup_requires_db(self, env):
        c = env["client"]
        assert c.post("/v1/admin/backup", headers=_auth(env["admin"])).status_code == 501
        assert c.post("/v1/admin/backup").status_code == 401

    def test_admin_settings(self, env):
        c, admin = env["client"], env["admin"]
        s = c.get("/v1/admin/settings", headers=_auth(admin))
        assert s.status_code == 200
        assert "rate_limit_rpm" in s.json()
        assert c.get("/v1/admin/settings").status_code == 401

    def test_admin_html_pages_unauthenticated(self, env):
        c = env["client"]
        for path in (
            "/admin",
            "/admin/health",
            "/admin/releases",
            "/admin/packages",
            "/admin/tokens",
            "/admin/audit",
            "/admin/principals",
        ):
            r = c.get(path)
            assert r.status_code == 200, path
            assert "text/html" in r.headers["content-type"]

    def test_admin_login_and_authenticated_pages(self, env):
        c, admin = env["client"], env["admin"]
        # Wrong token → 401 login page.
        bad = c.post("/admin/login", data={"token": "not-a-real-token"})
        assert bad.status_code == 401
        # A valid admin token mints a session cookie the client then reuses.
        good = c.post("/admin/login", data={"token": admin}, follow_redirects=False)
        assert good.status_code in (200, 302, 303)
        # Now the authenticated admin dashboard renders (different code path).
        dash = c.get("/admin")
        assert dash.status_code == 200
        health = c.get("/admin/health")
        assert health.status_code == 200

    def test_admin_packages_action_requires_session(self, env):
        # Without an admin session cookie the mutating form endpoint is forbidden.
        r = env["client"].post(
            "/admin/packages/action", data={"action": "yank", "name": "x", "version": "1"}
        )
        assert r.status_code == 403


# ── Organizations (DB-gated) ────────────────────────────────────


class TestOrganizationsGated:
    def test_list_and_get(self, env):
        c = env["client"]
        # Listing degrades gracefully to empty on the local backend.
        assert c.get("/v1/orgs").json() == {"total": 0, "organizations": []}
        # A specific org lookup 404s (no DB rows).
        assert c.get("/v1/orgs/anything").status_code == 404

    def test_create_requires_db(self, env):
        c, pub = env["client"], env["publisher"]
        r = c.post("/v1/orgs", json={"slug": "myorg", "display_name": "My Org"}, headers=_auth(pub))
        assert r.status_code == 501
        assert c.post("/v1/orgs", json={"slug": "x"}).status_code == 401
        assert (
            c.post("/v1/orgs", json={"slug": "x"}, headers=_auth(env["reader"])).status_code == 403
        )

    def test_logo_and_members_gated(self, env):
        c, admin = env["client"], env["admin"]
        # Logo GET 404s (no such org locally); member mutations need the DB.
        assert c.get("/v1/orgs/x/logo").status_code == 404
        # token_name is a query parameter; with the DB absent the handler 501s.
        assert (
            c.post(
                "/v1/orgs/x/members", params={"token_name": "y"}, headers=_auth(admin)
            ).status_code
            == 501
        )


# ── Tags (DB-gated for writes) ──────────────────────────────────


class TestTagsGated:
    def test_reads_degrade(self, env):
        c = env["client"]
        assert c.get("/v1/tags").json() == {"total": 0, "tags": []}
        assert c.get("/v1/tags/all").json() == {"tags": []}

    def test_writes_need_db(self, env):
        c, admin = env["client"], env["admin"]
        assert c.post("/v1/tags", json={"name": "utils"}, headers=_auth(admin)).status_code == 501
        assert (
            c.put("/v1/tags/utils", json={"description": "d"}, headers=_auth(admin)).status_code
            == 501
        )
        assert c.delete("/v1/tags/utils", headers=_auth(admin)).status_code == 501
        # Auth precedes the DB check.
        assert c.post("/v1/tags", json={"name": "x"}).status_code == 401
        assert (
            c.post("/v1/tags", json={"name": "x"}, headers=_auth(env["reader"])).status_code == 403
        )


# ── Mirrors (DB-gated) ──────────────────────────────────────────


class TestMirrorsGated:
    def test_endpoints_need_db(self, env):
        c, admin = env["client"], env["admin"]
        assert (
            c.post(
                "/v1/mirrors/register", json={"url": "https://m.example.com"}, headers=_auth(admin)
            ).status_code
            == 501
        )
        # Read listings degrade to an empty list on the local backend.
        assert c.get("/v1/mirrors").status_code == 200
        allm = c.get("/v1/mirrors/all", headers=_auth(admin))
        assert allm.status_code == 200 and allm.json()["total"] == 0
        assert (
            c.post(
                "/v1/mirrors/reject", params={"url": "https://m.example.com"}, headers=_auth(admin)
            ).status_code
            == 501
        )
        assert (
            c.request(
                "DELETE",
                "/v1/mirrors",
                params={"url": "https://m.example.com"},
                headers=_auth(admin),
            ).status_code
            == 501
        )

    def test_auth(self, env):
        c = env["client"]
        assert c.post("/v1/mirrors/register", json={"url": "https://m"}).status_code == 401
        assert (
            c.post(
                "/v1/mirrors/register", json={"url": "https://m"}, headers=_auth(env["reader"])
            ).status_code
            == 403
        )


# ── Builders & builds (DB-gated) ────────────────────────────────


class TestBuildersBuildsGated:
    def test_builders_need_db(self, env):
        c, pub = env["client"], env["publisher"]
        assert c.get("/v1/builders").status_code == 501
        assert c.get("/v1/builders/1").status_code == 501
        assert c.post(
            "/v1/builders/register",
            json={"hostname": "h", "platform": "linux", "arch": "x86_64"},
            headers=_auth(pub),
        ).status_code in (422, 501)

    def test_builds_need_db(self, env):
        c, pub, admin = env["client"], env["publisher"], env["admin"]
        assert c.post(
            "/v1/builds", json={"name": "zlib", "version": "1.0"}, headers=_auth(pub)
        ).status_code in (422, 501)
        assert c.get("/v1/builds", headers=_auth(pub)).status_code == 501
        assert c.get("/v1/builds/1").status_code in (401, 404, 501)
        # Auth on the list endpoint.
        assert c.get("/v1/builds").status_code == 401
        assert c.get("/v1/builds", headers=_auth(env["reader"])).status_code == 403


# ── CLI auth / device broker (DB-gated) ─────────────────────────


class TestCliAuthGated:
    def test_providers_open(self, env):
        r = env["client"].get("/v1/auth/providers")
        assert r.status_code == 200
        assert r.json() == {"providers": []}

    def test_device_and_authorize_need_db(self, env):
        c = env["client"]
        assert c.post("/v1/auth/device", json={"client_id": "cvcpkg-cli"}).status_code == 501
        assert c.post("/v1/auth/device/token", json={"pairing_id": "p"}).status_code == 501
        assert c.post("/v1/auth/device/cancel", json={"pairing_id": "p"}).status_code == 501
        assert (
            c.get(
                "/v1/auth/authorize",
                params={
                    "client_id": "cvcpkg-cli",
                    "redirect_uri": "http://127.0.0.1:9/callback",
                    "code_challenge": "x" * 43,
                },
                follow_redirects=False,
            ).status_code
            == 501
        )

    def test_whoami_and_devices(self, env):
        c, admin = env["client"], env["admin"]
        who = c.get("/v1/auth/whoami", headers=_auth(admin))
        assert who.status_code == 200
        assert who.json()["name"] == "admin-user"
        assert who.json()["role"] == "admin"
        assert c.get("/v1/auth/whoami").status_code == 401
        # Device listing requires the broker DB.
        assert c.get("/v1/auth/devices", headers=_auth(admin)).status_code == 501
        assert c.request("DELETE", "/v1/auth/devices/1", headers=_auth(admin)).status_code == 501

    def test_auth_revoke_needs_db(self, env):
        c = env["client"]
        assert (
            c.post("/v1/auth/revoke", json={"all": False}, headers=_auth(env["reader"])).status_code
            == 501
        )
        assert c.post("/v1/auth/revoke", json={"all": False}).status_code == 401
