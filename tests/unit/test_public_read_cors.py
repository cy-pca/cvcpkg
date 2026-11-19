"""Browser reads of the public endpoints, and the archive download's ETag.

A page on another origin (the libcvc web gallery) may GET the catalog, the
package listings and the archive downloads: those answer
``Access-Control-Allow-Origin: *`` and their preflight is answered.  Writes,
credentials and the rest of the API keep the allowlist.  A download carries
its sha256 as a strong ETag, and a client that holds the bytes gets a 304.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for these tests")

from fastapi.testclient import TestClient

from cvcpkg.server import app as app_module
from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

GALLERY = "https://transfix.github.io"
ADMIN_UI = "https://admin.example"
PAYLOAD = b"cvcpkg-archive" * 37
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
FN = "prop-crate-1.0.0+cvc.1-any-noarch-none-none.tar.zst"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """A DB-backed server with one public package whose archive is on disk,
    a publisher token, and ADMIN_UI on the CORS allowlist."""
    db_path = tmp_path / "cors.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.setattr(app_module, "CORS_ALLOWED_ORIGINS", [ADMIN_UI])

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbPackageIndex, DbTokenStore

    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / FN).write_bytes(PAYLOAD)

    async def _seed():
        init_db(db_url)
        await create_tables()
        await DbPackageIndex().add_package(
            name="prop-crate",
            version="1.0.0+cvc.1",
            platform="any",
            arch="noarch",
            build_type="none",
            link="none",
            sha256=SHA256,
            size_bytes=len(PAYLOAD),
            archive_url=f"/v1/download/{FN}",
        )
        token = await DbTokenStore(tmp_path).create("cors-publisher", TokenRole.publisher)
        await dispose_engine()
        return token

    token = asyncio.run(_seed())
    with TestClient(create_app(state_dir=tmp_path)) as client:
        yield client, token, db_path


def _downloads(db_path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM download_events").fetchone()[0]


class TestPublicReadCORS:
    @pytest.mark.parametrize(
        "path",
        [
            "/v1/catalog",
            "/v1/packages",
            "/v1/packages/prop-crate",
            "/v1/packages/prop-crate/latest?platform=any&arch=noarch&build_type=none&link=none",
            f"/v1/download/{FN}",
        ],
    )
    def test_any_origin_may_read(self, server, path):
        client, _token, _db = server
        r = client.get(path, headers={"Origin": GALLERY})
        assert r.status_code == 200, r.text
        assert r.headers["access-control-allow-origin"] == "*"
        assert "ETag" in r.headers["access-control-expose-headers"]
        assert "access-control-allow-credentials" not in r.headers

    def test_without_an_origin_too(self, server):
        # A shared cache may hand a browser what it stored for a non-browser client.
        client, _token, _db = server
        r = client.get(f"/v1/download/{FN}")
        assert r.headers["access-control-allow-origin"] == "*"

    def test_errors_are_readable(self, server):
        client, _token, _db = server
        r = client.get(
            "/v1/download/no-such-1.0-any-noarch-none-none.tar.zst", headers={"Origin": GALLERY}
        )
        assert r.status_code == 404
        assert r.headers["access-control-allow-origin"] == "*"

    def test_preflight_for_a_read(self, server):
        client, _token, _db = server
        r = client.options(
            f"/v1/download/{FN}",
            headers={
                "Origin": GALLERY,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "if-none-match",
            },
        )
        assert r.status_code == 204
        assert r.headers["access-control-allow-origin"] == "*"
        assert r.headers["access-control-allow-methods"] == "GET, HEAD"
        allowed = r.headers["access-control-allow-headers"].lower()
        assert "if-none-match" in allowed
        assert "authorization" not in allowed

    def test_preflight_for_a_write_keeps_the_allowlist(self, server):
        client, _token, _db = server
        r = client.options(
            "/v1/packages/prop-crate/1.0.0+cvc.1/yank",
            headers={"Origin": GALLERY, "Access-Control-Request-Method": "POST"},
        )
        assert r.status_code == 400  # CORSMiddleware: disallowed origin
        assert r.headers.get("access-control-allow-origin") != "*"

    def test_a_write_gets_no_wildcard(self, server):
        client, token, _db = server
        r = client.post(
            "/v1/packages/prop-crate/1.0.0+cvc.1/yank",
            headers={"Origin": GALLERY, "Authorization": f"Bearer {token}"},
        )
        assert "access-control-allow-origin" not in r.headers

    def test_other_reads_get_no_wildcard(self, server):
        client, _token, _db = server
        r = client.get("/v1/downloads/stats", headers={"Origin": GALLERY})
        assert "access-control-allow-origin" not in r.headers

    def test_an_allowlisted_origin_is_left_to_the_allowlist(self, server):
        client, _token, _db = server
        r = client.get("/v1/packages", headers={"Origin": ADMIN_UI})
        assert r.headers["access-control-allow-origin"] == ADMIN_UI
        r = client.options(
            "/v1/packages",
            headers={
                "Origin": ADMIN_UI,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == ADMIN_UI
        assert "authorization" in r.headers["access-control-allow-headers"].lower()


class TestDownloadETag:
    def test_the_etag_is_the_sha256(self, server):
        client, _token, _db = server
        r = client.get(f"/v1/download/{FN}")
        assert r.status_code == 200
        assert r.content == PAYLOAD
        assert r.headers["etag"] == f'"{SHA256}"'
        assert r.headers["cache-control"] == "no-cache"

    def test_a_client_holding_it_gets_a_304(self, server):
        client, _token, db_path = server
        before = _downloads(db_path)
        r = client.get(
            f"/v1/download/{FN}", headers={"If-None-Match": f'"{SHA256}"', "Origin": GALLERY}
        )
        assert r.status_code == 304
        assert r.content == b""
        assert r.headers["etag"] == f'"{SHA256}"'
        assert r.headers["access-control-allow-origin"] == "*"
        assert _downloads(db_path) == before, "a 304 is not a download"

    def test_another_etag_gets_the_bytes(self, server):
        client, _token, db_path = server
        before = _downloads(db_path)
        r = client.get(f"/v1/download/{FN}", headers={"If-None-Match": '"' + "0" * 64 + '"'})
        assert r.status_code == 200
        assert r.content == PAYLOAD
        assert _downloads(db_path) == before + 1

    def test_head_has_it_too(self, server):
        client, _token, _db = server
        r = client.head(f"/v1/download/{FN}")
        assert r.status_code == 200
        assert r.headers["etag"] == f'"{SHA256}"'
        r = client.head(f"/v1/download/{FN}", headers={"If-None-Match": f'"{SHA256}"'})
        assert r.status_code == 304

    def test_a_caller_keeps_it_out_of_shared_caches(self, server):
        client, token, _db = server
        r = client.get(f"/v1/download/{FN}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-cache"

    def test_the_live_row_wins_the_name(self, server):
        from cvcpkg.server.db_stores import DbPackageIndex

        idx = DbPackageIndex()

        async def _yanked_twin():
            # An older, yanked row that stored its archive under the same name.
            await idx.add_package(
                name="prop-crate",
                version="0.9.0+cvc.1",
                platform="any",
                arch="noarch",
                build_type="none",
                link="none",
                sha256="1" * 64,
                size_bytes=1,
                archive_url=f"/v1/download/{FN}",
            )
            await idx.yank("prop-crate", "0.9.0+cvc.1", platform="any", arch="noarch")

        asyncio.run(_yanked_twin())
        assert asyncio.run(idx.get_archive_sha256(FN)) == SHA256
        assert asyncio.run(idx.get_archive_sha256("missing.tar.zst")) == ""
