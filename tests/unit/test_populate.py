"""Upstream populate — the server-side import loop and --skip-existing.

``_populate_sync_once`` is exercised against a real sqlite-backed
package store with the upstream (httpx) faked, covering: import of
missing variants, dedup against existing/yanked local rows, placeholder
/ org / oversized / sha-mismatch skips, and archive materialization.
The ``builds submit-dag --skip-existing`` flag is tested with the same
mocked-httpx style as the other submit-dag tests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required for populate tests")

from cvcpkg.cli import main
from cvcpkg.server import app as app_mod

# ── Helpers ─────────────────────────────────────────────────────


def _bundle(name="zlib", version="1.3.1+cvc.3", platform="windows", arch="x86_64", **kw):
    b = {
        "name": name,
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": "release",
        "link": "shared",
        "sha256": "",
        "size_bytes": 4,
        "archive_url": f"/v1/download/{name}-{version}-{platform}-{arch}-release-shared.tar.zst",
        "published_at": "2026-07-14T00:00:00+00:00",
        "yanked": False,
        "signature": "",
        "key_fingerprint": "",
        "release_tag": "",
        "recipe_version": "abc123",
        "description": "test bundle",
        "homepage": "",
        "license": "Zlib",
        "maintainer": "",
        "tags": "",
        "published_by": "ci",
        "org": "",
        "required_deps": [{"name": "dep1", "version": "^1"}],
    }
    b.update(kw)
    return b


class _FakeAsyncResponse:
    def __init__(self, payload=None, content=b"data"):
        self._payload = payload
        self._content = content
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass

    async def aiter_bytes(self, n):
        yield self._content


class _FakeAsyncClient:
    """Fake httpx.AsyncClient serving a catalog + archive downloads."""

    def __init__(self, bundles, archives=None, **kw):
        self._bundles = bundles
        self._archives = archives or {}

    def __call__(self, **kw):  # constructor stand-in when monkeypatched
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        assert url.endswith("/v1/catalog"), url
        return _FakeAsyncResponse({"schema_version": 1, "bundles": self._bundles})

    def stream(self, method, url, **kw):
        content = b"data"
        for suffix, data in self._archives.items():
            if url.endswith(suffix):
                content = data
        resp = _FakeAsyncResponse(content=content)

        class _Ctx:
            async def __aenter__(self):
                return resp

            async def __aexit__(self, *a):
                pass

        return _Ctx()


@pytest.fixture()
def populate_env(tmp_path, monkeypatch):
    """Real sqlite package store + app-module globals wired for the loop."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'populate.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", "http://upstream.example")
    monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM_TOKEN", "")
    monkeypatch.setattr(app_mod, "POPULATE_MAX_PER_SYNC", 200)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbAuditLog, DbPackageIndex

    state = app_mod.ServerState(tmp_path, storage_uri="", require_auth_for_reads=False)
    monkeypatch.setattr(app_mod, "_state", state)
    monkeypatch.setattr(app_mod, "_use_db", True)

    def run(coro_fn, *bundles_and_archives):
        """Run *coro_fn* in a fresh loop with DB + fake upstream wired."""

        async def _inner():
            init_db(db_url)
            await create_tables()
            monkeypatch.setattr(app_mod, "_db_packages", DbPackageIndex())
            monkeypatch.setattr(app_mod, "_db_audit", DbAuditLog())
            try:
                return await coro_fn()
            finally:
                await dispose_engine()

        return asyncio.run(_inner())

    return run, tmp_path, monkeypatch


# ── _populate_sync_once ─────────────────────────────────────────


class TestPopulateSyncOnce:
    def test_imports_missing_bundle(self, populate_env):
        run, tmp_path, monkeypatch = populate_env
        import hashlib

        sha = hashlib.sha256(b"data").hexdigest()
        bundles = [_bundle(sha256=sha)]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            n = await app_mod._populate_sync_once()
            assert n == 1
            pkgs, total = await app_mod._db_packages.get_bundles(limit=10)
            assert total == 1
            p = pkgs[0]
            assert (p.name, p.platform, p.arch) == ("zlib", "windows", "x86_64")
            assert p.published_by == "populate:http://upstream.example"
            assert p.sha256 == sha
            return p.archive_url

        archive_url = run(_test)
        # Archive materialized on disk under the archives dir.
        fname = archive_url.rsplit("/", 1)[-1]
        assert (app_mod._get_state().archives_dir() / fname).read_bytes() == b"data"

    def test_existing_variant_not_reimported(self, populate_env):
        run, _, monkeypatch = populate_env
        bundles = [_bundle()]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            await app_mod._db_packages.add_package(
                name="zlib",
                version="1.3.1+cvc.3",
                platform="windows",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="x",
                size_bytes=1,
                archive_url="/v1/download/existing.tar.zst",
            )
            return await app_mod._populate_sync_once()

        assert run(_test) == 0

    def test_skips_placeholders_orgs_and_sha_mismatch(self, populate_env):
        run, _, monkeypatch = populate_env
        bundles = [
            _bundle(name="placeholder", archive_url=""),
            _bundle(name="orgpkg", org="someorg"),
            _bundle(name="badsha", sha256="0" * 64),
            _bundle(name="yankedpkg", yanked=True),
        ]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            n = await app_mod._populate_sync_once()
            _, total = await app_mod._db_packages.get_bundles(limit=10)
            return n, total

        assert run(_test) == (0, 0)

    def test_per_sync_cap(self, populate_env):
        run, _, monkeypatch = populate_env
        monkeypatch.setattr(app_mod, "POPULATE_MAX_PER_SYNC", 2)
        bundles = [_bundle(name=f"pkg{i}") for i in range(5)]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            return await app_mod._populate_sync_once()

        assert run(_test) == 2

    def test_oversized_bundle_skipped(self, populate_env):
        run, _, monkeypatch = populate_env
        monkeypatch.setattr(app_mod, "MAX_UPLOAD_BYTES", 10)
        bundles = [_bundle(name="huge", size_bytes=11)]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            return await app_mod._populate_sync_once()

        assert run(_test) == 0

    def test_required_deps_preserved(self, populate_env):
        run, _, monkeypatch = populate_env
        bundles = [_bundle()]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            await app_mod._populate_sync_once()
            cat = await app_mod._db_packages.get_catalog_dict()
            return cat["bundles"][0]["required_deps"]

        assert run(_test) == [{"name": "dep1", "version": "^1"}]


# ── builds submit-dag --skip-existing ───────────────────────────


def _write_recipe(tmp_path: Path, name: str, version="1.0.0", rev=2, deps=()):
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    deps_yaml = "".join(f"    - {d}\n" for d in deps)
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "{version}"
  cvc_revision: {rev}
depends:
  build:
{deps_yaml if deps else ""}
build:
  matrix:
    - platform: linux
      script: build.sh
    - platform: windows
      script: build.ps1
"""
    )
    return rdir


class TestSubmitDagSkipExisting:
    def _fake_client(self, published, posted_bodies):
        class FakeResp:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                if "/v1/builders" in url:
                    return FakeResp(
                        {
                            "builders": [
                                {
                                    "platform": "linux",
                                    "arch": "x86_64",
                                    "capabilities": {
                                        "cross_platforms": [
                                            {"platform": "windows", "arch": "x86_64"}
                                        ]
                                    },
                                }
                            ]
                        }
                    )
                if "/v1/packages" in url:
                    return FakeResp({"total": len(published), "packages": published})
                raise AssertionError(f"unexpected GET {url}")

            def post(self, url, json=None, **kw):
                posted_bodies.append(json)
                return FakeResp({"dag_id": "dag-t", "total": len(json["jobs"]), "jobs": []})

        return FakeClient

    def test_skips_published_variant(self, tmp_path, capsys, monkeypatch):
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        _write_recipe(tmp_path, "libpng", version="1.6.43", rev=1, deps=("zlib",))
        published = [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.3",
                "platform": "windows",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "archive_url": "/v1/download/zlib.tar.zst",
            }
        ]
        posted: list = []
        monkeypatch.setattr("httpx.Client", self._fake_client(published, posted))
        ret = main(
            [
                "builds",
                "submit-dag",
                "--server",
                "http://s.example",
                "--token",
                "tok",
                "--platform",
                "windows",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                "--skip-existing",
                "zlib",
                "libpng",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "already-published" in out
        (body,) = posted
        names = [j["recipe_name"] for j in body["jobs"]]
        assert names == ["libpng"]  # zlib skipped, dependent still builds
        assert body["jobs"][0]["depends_on"] == []  # dep satisfied by package

    def test_placeholder_rows_do_not_satisfy(self, tmp_path, capsys, monkeypatch):
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        published = [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.3",
                "platform": "windows",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "archive_url": "",  # placeholder — no artifacts
            }
        ]
        posted: list = []
        monkeypatch.setattr("httpx.Client", self._fake_client(published, posted))
        ret = main(
            [
                "builds",
                "submit-dag",
                "--server",
                "http://s.example",
                "--token",
                "tok",
                "--platform",
                "windows",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                "--skip-existing",
                "zlib",
            ]
        )
        assert ret == 0
        (body,) = posted
        assert [j["recipe_name"] for j in body["jobs"]] == ["zlib"]

    def test_version_mismatch_not_skipped(self, tmp_path, monkeypatch):
        # Published rev 2, recipe now at rev 3 — must rebuild.
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        published = [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.2",
                "platform": "windows",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "archive_url": "/v1/download/zlib.tar.zst",
            }
        ]
        posted: list = []
        monkeypatch.setattr("httpx.Client", self._fake_client(published, posted))
        ret = main(
            [
                "builds",
                "submit-dag",
                "--server",
                "http://s.example",
                "--token",
                "tok",
                "--platform",
                "windows",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                "--skip-existing",
                "zlib",
            ]
        )
        assert ret == 0
        (body,) = posted
        assert [j["recipe_name"] for j in body["jobs"]] == ["zlib"]


# ── healthz surface ─────────────────────────────────────────────


class TestHealthzPopulateFields:
    def test_healthz_reports_populate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "")
        monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", "http://upstream.example")
        monkeypatch.setattr(
            app_mod,
            "_populate_stats",
            {"last_sync": "t", "last_imported": 1, "imported_total": 2, "last_error": ""},
        )
        from fastapi.testclient import TestClient

        app = app_mod.create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            data = client.get("/healthz").json()
        assert data["populate_upstream"] == "http://upstream.example"
        assert data["populate_stats"]["imported_total"] == 2
        assert json.dumps(data)  # serializable
