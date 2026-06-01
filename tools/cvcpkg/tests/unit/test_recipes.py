"""Tests for Phase 4 — Recipe distribution (store and endpoints)."""

from __future__ import annotations

import asyncio
import io
import tarfile

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for recipe tests")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

# ── Helpers ─────────────────────────────────────────────────────


def _make_tar_gz(files: dict[str, str]) -> bytes:
    """Create a tar.gz in memory from a dict of {arcname: content}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin, publisher, and reader tokens."""
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
        reader_raw = await store.create("test-reader", TokenRole.reader)
        await dispose_engine()
        return admin_raw, pub_raw, reader_raw

    admin_token, pub_token, reader_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, reader_token, tmp_path


# ── DbRecipeStore unit tests ───────────────────────────────────


class TestDbRecipeStore:
    """Direct tests for the DbRecipeStore class."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "recipe_store.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        self._tmp = tmp_path
        yield

        async def _cleanup():
            await dispose_engine()

        asyncio.run(_cleanup())

    def _run(self, coro):
        return asyncio.run(coro)

    def test_upload_and_get(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            info = await store.upload(
                name="zlib",
                bundle_path="/tmp/zlib.tar.gz",
                bundle_size=1024,
                uploaded_by="admin",
                version="1.3.1",
            )
            assert info.name == "zlib"
            assert info.version == "1.3.1"
            assert info.bundle_size == 1024

            fetched = await store.get("zlib")
            assert fetched is not None
            assert fetched.name == "zlib"

        self._run(_test())

    def test_upload_updates_existing(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            info1 = await store.upload(
                name="zlib",
                bundle_path="/a.tar.gz",
                bundle_size=100,
                uploaded_by="admin1",
                version="1.0",
            )
            info2 = await store.upload(
                name="zlib",
                bundle_path="/b.tar.gz",
                bundle_size=200,
                uploaded_by="admin2",
                version="2.0",
            )
            assert info2.id == info1.id
            assert info2.version == "2.0"
            assert info2.bundle_size == 200

        self._run(_test())

    def test_get_not_found(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            result = await store.get("nonexistent")
            assert result is None

        self._run(_test())

    def test_same_name_different_orgs(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            g = await store.upload(
                name="zlib",
                bundle_path="/g.tar.gz",
                bundle_size=10,
                uploaded_by="admin",
            )
            o = await store.upload(
                name="zlib",
                bundle_path="/o.tar.gz",
                bundle_size=20,
                uploaded_by="admin",
                org_slug="myorg",
            )
            assert g.id != o.id
            assert g.org_slug == ""
            assert o.org_slug == "myorg"

        self._run(_test())

    def test_list_recipes(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            await store.upload(
                name="boost",
                bundle_path="/b.tar.gz",
                bundle_size=100,
                uploaded_by="admin",
            )
            await store.upload(
                name="zlib",
                bundle_path="/z.tar.gz",
                bundle_size=50,
                uploaded_by="admin",
            )
            recipes, total = await store.list_recipes()
            assert total == 2
            assert len(recipes) == 2
            # Ordered by name ascending
            assert recipes[0].name == "boost"
            assert recipes[1].name == "zlib"

        self._run(_test())

    def test_list_recipes_org_filter(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            await store.upload(
                name="zlib",
                bundle_path="/z.tar.gz",
                bundle_size=50,
                uploaded_by="admin",
            )
            await store.upload(
                name="zlib",
                bundle_path="/zo.tar.gz",
                bundle_size=60,
                uploaded_by="admin",
                org_slug="myorg",
            )
            recipes, total = await store.list_recipes(org_slug="myorg")
            assert total == 1
            assert recipes[0].org_slug == "myorg"

        self._run(_test())

    def test_list_recipes_empty(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            recipes, total = await store.list_recipes()
            assert total == 0
            assert recipes == []

        self._run(_test())

    def test_list_recipes_pagination(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            for name in ["a", "b", "c", "d", "e"]:
                await store.upload(
                    name=name,
                    bundle_path=f"/{name}.tar.gz",
                    bundle_size=10,
                    uploaded_by="admin",
                )
            page1, total = await store.list_recipes(limit=2, offset=0)
            assert total == 5
            assert len(page1) == 2
            page3, _ = await store.list_recipes(limit=2, offset=4)
            assert len(page3) == 1

        self._run(_test())

    def test_delete(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            await store.upload(
                name="zlib",
                bundle_path="/z.tar.gz",
                bundle_size=50,
                uploaded_by="admin",
            )
            deleted = await store.delete("zlib")
            assert deleted is True
            assert await store.get("zlib") is None

        self._run(_test())

    def test_delete_not_found(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            deleted = await store.delete("nonexistent")
            assert deleted is False

        self._run(_test())

    def test_get_bundle_path(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            await store.upload(
                name="zlib",
                bundle_path="/data/zlib.tar.gz",
                bundle_size=50,
                uploaded_by="admin",
            )
            path = await store.get_bundle_path("zlib")
            assert path == "/data/zlib.tar.gz"

        self._run(_test())

    def test_get_bundle_path_not_found(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            path = await store.get_bundle_path("nonexistent")
            assert path is None

        self._run(_test())

    def test_upload_with_hash(self):
        from cvcpkg.server.db_stores import DbRecipeStore

        async def _test():
            store = DbRecipeStore()
            info = await store.upload(
                name="zlib",
                bundle_path="/z.tar.gz",
                bundle_size=50,
                uploaded_by="admin",
                recipe_hash="abc123",
            )
            assert info.recipe_hash == "abc123"

        self._run(_test())


# ── API endpoint tests ──────────────────────────────────────────


class TestRecipeEndpoints:
    """Test recipe distribution REST endpoints."""

    def _upload(self, client, token, name="zlib", org_slug=""):
        bundle = _make_tar_gz({"recipe.yaml": f"name: {name}\n"})
        params = {"org_slug": org_slug, "version": "1.0"}
        return client.post(
            f"/v1/recipes/{name}",
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_upload_recipe(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = self._upload(client, admin_tok)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "zlib"
        assert data["version"] == "1.0"
        assert data["bundle_size"] > 0

    def test_upload_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = self._upload(client, pub_tok)
        assert resp.status_code == 403

    def test_upload_invalid_name(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        bundle = _make_tar_gz({"recipe.yaml": "name: bad\n"})
        resp = client.post(
            "/v1/recipes/-bad-name",
            files={"file": ("bad.tar.gz", bundle, "application/gzip")},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 400

    def test_upload_overwrites(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok)
        # Upload again with different version
        bundle = _make_tar_gz({"recipe.yaml": "name: zlib\n"})
        resp = client.post(
            "/v1/recipes/zlib",
            files={"file": ("zlib.tar.gz", bundle, "application/gzip")},
            params={"version": "2.0"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == "2.0"

    def test_list_recipes_empty(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.get(
            "/v1/recipes",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["recipes"] == []

    def test_list_recipes(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok, "zlib")
        self._upload(client, admin_tok, "boost")

        resp = client.get(
            "/v1/recipes",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_download_recipe(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok)

        resp = client.get(
            "/v1/recipes/zlib",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"

    def test_download_not_found(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.get(
            "/v1/recipes/nonexistent",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_delete_recipe(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok)

        resp = client.delete(
            "/v1/recipes/zlib",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify gone
        resp = client.get(
            "/v1/recipes/zlib",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_delete_requires_admin(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok)
        resp = client.delete(
            "/v1/recipes/zlib",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 403

    def test_delete_not_found(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.delete(
            "/v1/recipes/nonexistent",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 404

    def test_reader_cannot_list(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.get(
            "/v1/recipes",
            headers={"Authorization": f"Bearer {reader_tok}"},
        )
        assert resp.status_code == 403

    def test_reader_cannot_download(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.get(
            "/v1/recipes/zlib",
            headers={"Authorization": f"Bearer {reader_tok}"},
        )
        assert resp.status_code == 403

    def test_org_scoped_upload_and_download(self, db_server_env):
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        # Upload to org scope
        resp = self._upload(client, admin_tok, "zlib", org_slug="myorg")
        assert resp.status_code == 200
        assert resp.json()["org_slug"] == "myorg"

        # Download with org_slug
        resp = client.get(
            "/v1/recipes/zlib",
            params={"org_slug": "myorg"},
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 200

        # Without org_slug, should 404
        resp = client.get(
            "/v1/recipes/zlib",
            headers={"Authorization": f"Bearer {pub_tok}"},
        )
        assert resp.status_code == 404

    def test_requires_auth(self, db_server_env):
        client, *_ = db_server_env
        resp = client.get("/v1/recipes")
        assert resp.status_code == 401


# ── GET /v1/recipes/bundle ──────────────────────────────────────


class TestRecipeBundleEndpoint:
    """Test the combined recipe bundle download endpoint."""

    def _upload(self, client, token, name, org_slug=""):
        bundle = _make_tar_gz({
            f"{name}/recipe.yaml": f"name: {name}\nupstream_version: '1.0'\n",
            f"{name}/build.sh": f"#!/bin/sh\necho building {name}\n",
        })
        params = {"org_slug": org_slug, "version": "1.0"}
        return client.post(
            f"/v1/recipes/{name}",
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_bundle_empty_db(self, db_server_env):
        """Bundle from empty DB returns a valid (empty) tar.gz."""
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.get("/v1/recipes/bundle")
        assert resp.status_code == 200
        assert "gzip" in resp.headers.get("content-type", "")
        # Should be a valid tarball with no members
        content = resp.content
        assert len(content) > 0
        extracted = tarfile.open(fileobj=io.BytesIO(content), mode="r:gz")
        assert extracted.getnames() == []

    def test_bundle_contains_uploaded_recipes(self, db_server_env):
        """After uploading recipes, the bundle contains them all."""
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok, "zlib")
        self._upload(client, admin_tok, "boost")

        resp = client.get("/v1/recipes/bundle")
        assert resp.status_code == 200
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            names = tar.getnames()
        # Both recipes should appear in the bundle
        assert any("zlib" in n for n in names)
        assert any("boost" in n for n in names)

    def test_bundle_no_auth_required(self, db_server_env):
        """The bundle endpoint does not require authentication."""
        client, admin_tok, *_ = db_server_env
        self._upload(client, admin_tok, "zlib")
        resp = client.get("/v1/recipes/bundle")
        assert resp.status_code == 200

    def test_bundle_content_disposition(self, db_server_env):
        """Response includes a Content-Disposition header."""
        client, *_ = db_server_env
        resp = client.get("/v1/recipes/bundle")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "recipes.tar.gz" in cd

    def test_bundle_org_filter(self, db_server_env):
        """Bundle with org_slug only includes that org's recipes."""
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload(client, admin_tok, "zlib")
        self._upload(client, admin_tok, "orgpkg", org_slug="myorg")

        resp = client.get("/v1/recipes/bundle", params={"org_slug": "myorg"})
        assert resp.status_code == 200
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("orgpkg" in n for n in names)
        # base-set recipe should NOT be here
        assert not any("zlib" in n for n in names)

    def test_bundle_filesystem_fallback(self, tmp_path, monkeypatch):
        """Without a DB, the bundle endpoint serves from local recipes dir."""
        # Ensure CVCPKG_DATABASE_URL is unset
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)

        # Create a fake recipes directory
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        zlib_dir = recipes_dir / "zlib"
        zlib_dir.mkdir()
        (zlib_dir / "recipe.yaml").write_text("name: zlib\n")
        (zlib_dir / "build.sh").write_text("echo hi\n")

        # Monkey-patch find_recipes_dir to return our dir
        monkeypatch.setattr(
            "cvcpkg.builder.find_recipes_dir", lambda: recipes_dir
        )

        from cvcpkg.server.app import create_app
        from cvcpkg.server.auth import TokenStore

        store = TokenStore(tmp_path / "state")
        admin_tok = store.create("admin", __import__("cvcpkg.server.models", fromlist=["TokenRole"]).TokenRole.admin)
        app = create_app(state_dir=tmp_path / "state")
        with TestClient(app) as client:
            resp = client.get("/v1/recipes/bundle")
        assert resp.status_code == 200
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("zlib" in n for n in names)


# ── POST /v1/recipes/{name}/register ───────────────────────────


class TestRecipeRegisterEndpoint:
    """Test the recipe placeholder registration endpoint."""

    def _upload_recipe(self, client, token, name):
        bundle = _make_tar_gz({
            f"{name}/recipe.yaml": f"name: {name}\nupstream_version: '1.0'\n",
        })
        return client.post(
            f"/v1/recipes/{name}",
            files={"file": (f"{name}.tar.gz", bundle, "application/gzip")},
            params={"version": "1.0"},
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_register_placeholder(self, db_server_env):
        """Registering a recipe creates a placeholder in the catalog."""
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload_recipe(client, admin_tok, "zlib")

        resp = client.post(
            "/v1/recipes/zlib/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={
                "version": "1.3.1+cvc.1",
                "description": "zlib compression",
                "homepage": "https://zlib.net",
                "license": "zlib",
                "maintainer": "test",
                "org_slug": "",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "registered"
        assert data["name"] == "zlib"

    def test_register_requires_admin(self, db_server_env):
        """Only admins can register recipe placeholders."""
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        resp = client.post(
            "/v1/recipes/zlib/register",
            headers={"Authorization": f"Bearer {pub_tok}"},
            json={"version": "1.0+cvc.1"},
        )
        assert resp.status_code == 403

    def test_register_invalid_name(self, db_server_env):
        """Invalid recipe names are rejected."""
        client, admin_tok, *_ = db_server_env
        resp = client.post(
            "/v1/recipes/-bad/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"version": "1.0+cvc.1"},
        )
        assert resp.status_code == 400

    def test_register_no_auth(self, db_server_env):
        """Unauthenticated requests are rejected."""
        client, *_ = db_server_env
        resp = client.post(
            "/v1/recipes/zlib/register",
            json={"version": "1.0+cvc.1"},
        )
        assert resp.status_code == 401

    def test_register_appears_in_catalog(self, db_server_env):
        """Registered placeholder should appear in the catalog."""
        client, admin_tok, pub_tok, reader_tok, _ = db_server_env
        self._upload_recipe(client, admin_tok, "zlib")

        client.post(
            "/v1/recipes/zlib/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={
                "version": "1.3.1+cvc.1",
                "description": "compression library",
            },
        )

        # Check catalog for the placeholder
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        catalog_text = resp.text
        assert "zlib" in catalog_text

    def test_register_already_built_noop(self, db_server_env):
        """If a built package exists, register returns 'already_built'."""
        client, admin_tok, pub_tok, reader_tok, tmp_path = db_server_env
        # First, publish a real package with an archive via the publish endpoint
        archive_data = _make_tar_gz({"manifest.yaml": "name: zlib\n"})

        resp = client.post(
            "/v1/publish",
            params={
                "name": "zlib",
                "version": "1.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
            },
            files={"file": ("zlib.tar.gz", io.BytesIO(archive_data))},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 200, resp.text

        # Now try to register — should see already_built
        self._upload_recipe(client, admin_tok, "zlib")
        resp = client.post(
            "/v1/recipes/zlib/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"version": "1.0+cvc.1"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_built"

    def test_register_with_org(self, db_server_env):
        """Register with org_slug sets org on the placeholder."""
        client, admin_tok, *_ = db_server_env
        self._upload_recipe(client, admin_tok, "mypkg")

        resp = client.post(
            "/v1/recipes/mypkg/register",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={
                "version": "2.0+cvc.1",
                "org_slug": "myorg",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"
