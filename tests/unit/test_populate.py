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

    def test_local_org_pkg_does_not_shadow_public_upstream(self, populate_env):
        """A private org package must not shadow a public upstream package that
        shares its variant key: org packages are a separate namespace, so
        populate must still import the public one.  Regression for the
        populate-diff collision bug (the diff set omitted org_slug)."""
        run, _, monkeypatch = populate_env
        import hashlib

        sha = hashlib.sha256(b"data").hexdigest()
        monkeypatch.setattr(
            "httpx.AsyncClient", lambda **kw: _FakeAsyncClient([_bundle(sha256=sha)])
        )

        async def _test():
            # Pre-existing LOCAL org package sharing the upstream variant key.
            await app_mod._db_packages.add_package(
                name="zlib",
                version="1.3.1+cvc.3",
                platform="windows",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="localorg",
                size_bytes=1,
                archive_url="/v1/download/local-org.tar.zst",
                org_slug="acme",
            )
            n = await app_mod._populate_sync_once()
            pkgs, total = await app_mod._db_packages.get_bundles(limit=10)
            return n, total, sorted((p.org, p.published_by) for p in pkgs)

        n, total, rows = run(_test)
        assert n == 1  # public upstream variant imported despite the org row
        assert total == 2
        assert ("", "populate:http://upstream.example") in rows  # public one landed
        assert any(org == "acme" for org, _ in rows)  # org one untouched

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

    def test_platform_allowlist(self, populate_env):
        """Only platforms in CVCPKG_POPULATE_PLATFORMS are imported."""
        run, _, monkeypatch = populate_env
        monkeypatch.setenv("CVCPKG_POPULATE_PLATFORMS", "linux,windows")
        bundles = [
            _bundle(name="a", platform="linux"),
            _bundle(name="b", platform="windows"),
            _bundle(name="c", platform="macos"),
            _bundle(name="d", platform="freebsd"),
        ]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            n = await app_mod._populate_sync_once()
            pkgs, _ = await app_mod._db_packages.get_bundles(limit=10)
            return n, sorted(p.platform for p in pkgs)

        assert run(_test) == (2, ["linux", "windows"])

    def test_empty_allowlist_imports_all_platforms(self, populate_env):
        run, _, monkeypatch = populate_env
        monkeypatch.delenv("CVCPKG_POPULATE_PLATFORMS", raising=False)
        bundles = [
            _bundle(name="a", platform="linux"),
            _bundle(name="c", platform="macos"),
        ]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            return await app_mod._populate_sync_once()

        assert run(_test) == 2

    def test_mirror_exclude_denylist(self, populate_env):
        """CVCPKG_POPULATE_EXCLUDE packages are never mirrored (Phase 12)."""
        run, _, monkeypatch = populate_env
        monkeypatch.setenv("CVCPKG_POPULATE_EXCLUDE", "qt6,vtk")
        bundles = [
            _bundle(name="boost", platform="linux"),
            _bundle(name="qt6", platform="linux"),
            _bundle(name="vtk", platform="linux"),
        ]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            n = await app_mod._populate_sync_once()
            pkgs, _ = await app_mod._db_packages.get_bundles(limit=10)
            return n, sorted(p.name for p in pkgs)

        assert run(_test) == (1, ["boost"])

    def test_mirror_include_allowlist(self, populate_env):
        """With CVCPKG_POPULATE_INCLUDE set, only listed packages mirror."""
        run, _, monkeypatch = populate_env
        monkeypatch.setenv("CVCPKG_POPULATE_INCLUDE", "boost,zlib")
        bundles = [
            _bundle(name="boost", platform="linux"),
            _bundle(name="zlib", platform="linux"),
            _bundle(name="qt6", platform="linux"),
        ]
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(bundles))

        async def _test():
            n = await app_mod._populate_sync_once()
            pkgs, _ = await app_mod._db_packages.get_bundles(limit=10)
            return n, sorted(p.name for p in pkgs)

        assert run(_test) == (2, ["boost", "zlib"])


# ── builds submit-dag --skip-existing ───────────────────────────


def _write_recipe(tmp_path: Path, name: str, version="1.0.0", rev=2, deps=(), timeout=None):
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    deps_yaml = "".join(f"    - {d}\n" for d in deps)
    timeout_line = f"  timeout_seconds: {timeout}\n" if timeout is not None else ""
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
{timeout_line}  matrix:
    - platform: linux
      script: build.sh
    - platform: windows
      script: build.ps1
"""
    )
    return rdir


def _write_any_recipe(tmp_path: Path, name: str, version="1.0.0", rev=1, deps=()):
    """A platform-independent (`platform: any`) recipe."""
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
    - platform: any
      script: build.sh
"""
    )
    return rdir


class TestSubmitDagNoarch:
    """A `platform: any` recipe is scheduled ONCE as any/noarch, not per host."""

    def _fake_client(self, posted_bodies):
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
                    return FakeResp({"total": 0, "packages": []})
                raise AssertionError(f"unexpected GET {url}")

            def post(self, url, json=None, **kw):
                posted_bodies.append(json)
                return FakeResp({"dag_id": "dag-t", "total": len(json["jobs"]), "jobs": []})

        return FakeClient

    def _submit(self, tmp_path, monkeypatch, posted, *recipes):
        monkeypatch.setattr("httpx.Client", self._fake_client(posted))
        return main(
            [
                "builds",
                "submit-dag",
                "--server",
                "http://s.example",
                "--token",
                "tok",
                "--platform",
                "linux,windows",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                *recipes,
            ]
        )

    def test_any_recipe_scheduled_once_as_noarch(self, tmp_path, monkeypatch):
        _write_any_recipe(tmp_path, "idna")
        # anyio (any) depends on idna (any) + python312 (concrete, not submitted)
        _write_any_recipe(tmp_path, "anyio", deps=("idna", "python312"))
        _write_recipe(tmp_path, "asyncpg-cp311", version="0.31.0", rev=1)

        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, posted, "idna", "anyio", "asyncpg-cp311")
        assert ret == 0

        def _names(body):
            return {j["recipe_name"] for j in body["jobs"]}

        noarch_bodies = [b for b in posted if all(j["platform"] == "any" for j in b["jobs"])]
        concrete_bodies = [b for b in posted if any(j["platform"] != "any" for j in b["jobs"])]

        # The two 'any' recipes land in exactly ONE noarch DAG, not per host.
        assert len(noarch_bodies) == 1
        (noarch,) = noarch_bodies
        assert _names(noarch) == {"idna", "anyio"}
        for j in noarch["jobs"]:
            assert (j["platform"], j["arch"]) == ("any", "noarch")

        # Intra-'any' dependency edge preserved; the concrete build dep
        # (python312, not in this DAG) is simply omitted rather than dangling.
        idx = {j["recipe_name"]: i for i, j in enumerate(noarch["jobs"])}
        anyio_job = noarch["jobs"][idx["anyio"]]
        assert anyio_job["depends_on"] == [idx["idna"]]

        # The 'any' recipes never appear in a per-host DAG.
        for b in concrete_bodies:
            assert not (_names(b) & {"idna", "anyio"})
        # The concrete C-ext recipe fans out per host as before.
        assert concrete_bodies, "expected per-host DAGs for the concrete recipe"
        for b in concrete_bodies:
            assert _names(b) == {"asyncpg-cp311"}

    def test_only_any_recipes_submits_just_the_noarch_dag(self, tmp_path, monkeypatch):
        _write_any_recipe(tmp_path, "certifi")
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, posted, "certifi")
        assert ret == 0
        # No concrete DAG at all — one noarch DAG carrying certifi.
        assert len(posted) == 1
        (body,) = posted
        assert [(j["recipe_name"], j["platform"], j["arch"]) for j in body["jobs"]] == [
            ("certifi", "any", "noarch")
        ]


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

    def test_skip_existing_on_by_default(self, tmp_path, capsys, monkeypatch):
        # No --skip-existing flag: default-on skips the already-published
        # variant, so a bare submit "fills the gaps" instead of rebuilding.
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
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
                "zlib",
            ]
        )
        assert ret == 0
        assert "already-published" in capsys.readouterr().out
        assert posted == []  # nothing to build — the variant already exists

    def test_no_skip_existing_forces_rebuild(self, tmp_path, monkeypatch):
        # --no-skip-existing overrides the default and rebuilds the published
        # variant.
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
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
                "--no-skip-existing",
                "zlib",
            ]
        )
        assert ret == 0
        (body,) = posted
        assert [j["recipe_name"] for j in body["jobs"]] == ["zlib"]

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


# ── mirror size budget + usage-based eviction (Phase 12 increment 2) ──


class TestMirrorEviction:
    async def _add(self, name, size, *, published_by="populate:up", org_slug="", downloads=0):
        await app_mod._db_packages.add_package(
            name=name,
            version="1.0.0",
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="s" * 64,
            size_bytes=size,
            archive_url=f"/v1/download/{name}.tar.zst",
            published_by=published_by,
            org_slug=org_slug,
        )
        for _ in range(downloads):
            await app_mod._db_downloads.record(name, "1.0.0", "linux")

    def test_evicts_least_downloaded_over_budget(self, populate_env):
        run, _, monkeypatch = populate_env
        monkeypatch.setattr(app_mod, "POPULATE_MAX_MIRROR_BYTES", 250)

        async def _test():
            from cvcpkg.server.db_stores import DbDownloadStore

            monkeypatch.setattr(app_mod, "_db_downloads", DbDownloadStore())
            # 3 populate packages, 100 bytes each (total 300 > budget 250).
            await self._add("popular", 100, downloads=50)
            await self._add("rare", 100, downloads=0)
            await self._add("medium", 100, downloads=5)
            evicted = await app_mod._enforce_mirror_budget()
            pkgs, _ = await app_mod._db_packages.get_bundles(limit=10)
            return evicted, sorted(p.name for p in pkgs)

        evicted, remaining = run(_test)
        assert evicted == 1
        assert "rare" not in remaining  # least-downloaded evicted
        assert "popular" in remaining and "medium" in remaining

    def test_never_evicts_org_or_local_packages(self, populate_env):
        run, _, monkeypatch = populate_env
        monkeypatch.setattr(app_mod, "POPULATE_MAX_MIRROR_BYTES", 50)

        async def _test():
            from cvcpkg.server.db_stores import DbDownloadStore

            monkeypatch.setattr(app_mod, "_db_downloads", DbDownloadStore())
            # Way over a tiny budget, but none of these are evictable:
            await self._add("orgpkg", 100, org_slug="acme", downloads=0)
            await self._add("localpub", 100, published_by="alice", downloads=0)
            evicted = await app_mod._enforce_mirror_budget()
            pkgs, _ = await app_mod._db_packages.get_bundles(limit=10)
            return evicted, sorted(p.name for p in pkgs)

        evicted, remaining = run(_test)
        assert evicted == 0  # org + locally-published are never evicted
        assert remaining == ["localpub", "orgpkg"]

    def test_no_budget_evicts_nothing(self, populate_env):
        run, _, monkeypatch = populate_env
        monkeypatch.setattr(app_mod, "POPULATE_MAX_MIRROR_BYTES", 0)

        async def _test():
            from cvcpkg.server.db_stores import DbDownloadStore

            monkeypatch.setattr(app_mod, "_db_downloads", DbDownloadStore())
            await self._add("a", 10**9, downloads=0)
            return await app_mod._enforce_mirror_budget()

        assert run(_test) == 0


# ── builds submit-dag auto-deps (auto-add unpublished dependencies) ──


class TestSubmitDagAutoDeps:
    """submit-dag pulls UNpublished, buildable deps into the DAG (default on).

    A dependency that is a catalog gap builds first instead of failing the
    dependent late at install/configure; an already-published dep is left for
    the builder to fetch; an unpublished dep with no recipe is a hard error.
    """

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
                    return FakeResp({"builders": [{"platform": "linux", "arch": "x86_64"}]})
                if "/v1/packages" in url:
                    return FakeResp({"total": len(published), "packages": published})
                raise AssertionError(f"unexpected GET {url}")

            def post(self, url, json=None, **kw):
                posted_bodies.append(json)
                return FakeResp({"dag_id": "dag-t", "total": len(json["jobs"]), "jobs": []})

        return FakeClient

    def _submit(self, tmp_path, monkeypatch, published, posted, *args):
        monkeypatch.setattr("httpx.Client", self._fake_client(published, posted))
        return main(
            [
                "builds",
                "submit-dag",
                "--server",
                "http://s.example",
                "--token",
                "tok",
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(tmp_path / "recipes"),
                "--no-default-recipes",
                *args,
            ]
        )

    @staticmethod
    def _pub(name, version, plat="linux", arch="x86_64", link="shared"):
        return {
            "name": name,
            "version": version,
            "platform": plat,
            "arch": arch,
            "build_type": "release",
            "link": link,
            "archive_url": f"/v1/download/{name}.tar.zst",
        }

    def test_unpublished_dep_is_auto_added(self, tmp_path, capsys, monkeypatch):
        # libpng build-deps zlib; neither is published → both build, zlib first.
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        _write_recipe(tmp_path, "libpng", version="1.6.43", rev=1, deps=("zlib",))
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "libpng")
        assert ret == 0
        assert "Auto-added" in capsys.readouterr().out
        (body,) = posted
        idx = {j["recipe_name"]: i for i, j in enumerate(body["jobs"])}
        assert set(idx) == {"libpng", "zlib"}
        assert body["jobs"][idx["libpng"]]["depends_on"] == [idx["zlib"]]
        assert body["jobs"][idx["zlib"]]["depends_on"] == []

    def test_transitive_gap_is_auto_added(self, tmp_path, monkeypatch):
        # libpng → zlib → lzma; all unpublished → all build, in order.
        _write_recipe(tmp_path, "lzma", version="5.6.0", rev=1)
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3, deps=("lzma",))
        _write_recipe(tmp_path, "libpng", version="1.6.43", rev=1, deps=("zlib",))
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "libpng")
        assert ret == 0
        (body,) = posted
        idx = {j["recipe_name"]: i for i, j in enumerate(body["jobs"])}
        assert set(idx) == {"libpng", "zlib", "lzma"}
        assert body["jobs"][idx["zlib"]]["depends_on"] == [idx["lzma"]]
        assert body["jobs"][idx["libpng"]]["depends_on"] == [idx["zlib"]]

    def test_published_dep_not_added(self, tmp_path, monkeypatch):
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        _write_recipe(tmp_path, "libpng", version="1.6.43", rev=1, deps=("zlib",))
        published = [self._pub("zlib", "1.3.1+cvc.3")]
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, published, posted, "libpng")
        assert ret == 0
        (body,) = posted
        # zlib is in the catalog → builder fetches it, not rebuilt.
        assert [j["recipe_name"] for j in body["jobs"]] == ["libpng"]
        assert body["jobs"][0]["depends_on"] == []

    def test_no_deps_flag_disables_autoadd(self, tmp_path, monkeypatch):
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        _write_recipe(tmp_path, "libpng", version="1.6.43", rev=1, deps=("zlib",))
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "--no-deps", "libpng")
        assert ret == 0
        (body,) = posted
        assert [j["recipe_name"] for j in body["jobs"]] == ["libpng"]

    def test_unbuildable_gap_is_hard_error(self, tmp_path, capsys, monkeypatch):
        # libpng deps 'mysterylib' — no recipe, not published → fail up front.
        _write_recipe(tmp_path, "libpng", version="1.6.43", rev=1, deps=("mysterylib",))
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "libpng")
        assert ret == 1
        assert "mysterylib" in capsys.readouterr().err
        assert not posted  # nothing submitted

    def test_cross_noarch_dep_warned_not_added(self, tmp_path, capsys, monkeypatch):
        # A concrete recipe depending on an UNpublished 'any' recipe: the noarch
        # dep can't be scheduled in the concrete DAG → warn, don't add.
        _write_any_recipe(tmp_path, "certifi", version="2024.2.2", rev=1)
        _write_recipe(tmp_path, "foo", version="1.0.0", rev=1, deps=("certifi",))
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "foo")
        assert ret == 0
        out = capsys.readouterr().out
        assert "noarch dependency" in out and "certifi" in out
        (body,) = posted
        assert [j["recipe_name"] for j in body["jobs"]] == ["foo"]

    def test_recipe_timeout_propagated_to_jobs(self, tmp_path, monkeypatch):
        # A recipe declaring build.timeout_seconds propagates it into its job;
        # a recipe without one omits the field (server applies its default).
        _write_recipe(tmp_path, "llvm18", version="18.1.8", rev=1, timeout=18000)
        _write_recipe(tmp_path, "zlib", version="1.3.1", rev=3)
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "llvm18", "zlib")
        assert ret == 0
        (body,) = posted
        jobs = {j["recipe_name"]: j for j in body["jobs"]}
        assert jobs["llvm18"]["timeout_seconds"] == 18000
        assert "timeout_seconds" not in jobs["zlib"]

    def test_auto_added_dep_carries_its_timeout(self, tmp_path, monkeypatch):
        # An unpublished dep auto-added into the DAG keeps its own recipe timeout.
        _write_recipe(tmp_path, "llvm18", version="18.1.8", rev=1, timeout=18000)
        _write_recipe(tmp_path, "shiboken6", version="6.8.2", rev=1, deps=("llvm18",))
        posted: list = []
        ret = self._submit(tmp_path, monkeypatch, [], posted, "shiboken6")
        assert ret == 0
        (body,) = posted
        jobs = {j["recipe_name"]: j for j in body["jobs"]}
        assert set(jobs) == {"shiboken6", "llvm18"}  # llvm18 auto-added
        assert jobs["llvm18"]["timeout_seconds"] == 18000
