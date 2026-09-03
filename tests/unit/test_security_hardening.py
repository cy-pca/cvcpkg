"""Security-hardening regression tests.

Covers the fixes from the cvcpkg security audit:

* reflected XSS via the SPA JS-string encoder (token theft)
* tar-slip / link-escape rejection in archive extraction
* self-registration role-clamp + rate limiting
* admin-only mirror registration (unauth SSRF / mirror poisoning)
* recipe-namespace path traversal + cross-tenant recipe visibility
* private-org package leaks via /v1/cache, /v1/feed.xml, and archive download
* build-job / build-log org isolation (read *and* submit paths)

Each server test uses a fresh function-scoped DB-backed app with four
principals: ``admin``, ``owner`` (publisher; member of a public ``acme`` and a
private ``shell`` org), ``stranger`` (publisher; no memberships), ``reader``.
"""

from __future__ import annotations

import asyncio
import io
import tarfile

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server import app as app_mod
from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tar_bytes(members: list[tuple[str, bytes]], mode: str = "w:gz") -> bytes:
    """Build an in-memory tar with the given (name, content) members."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in members:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


_GOOD_RECIPE = _tar_bytes([("librecipe/recipe.yaml", b"name: librecipe\n")])


@pytest.fixture()
def sec_server(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sec.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    monkeypatch.delenv("CVCPKG_POPULATE_UPSTREAM", raising=False)
    monkeypatch.setattr(app_mod, "POPULATE_UPSTREAM", "", raising=False)
    monkeypatch.setattr(app_mod, "MIRROR_MODE", False, raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("admin", TokenRole.admin)
        owner = await store.create("owner", TokenRole.publisher)
        stranger = await store.create("stranger", TokenRole.publisher)
        reader = await store.create("reader", TokenRole.reader)
        await dispose_engine()
        return admin, owner, stranger, reader

    admin, owner, stranger, reader = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        for slug, priv in (("acme", False), ("shell", True)):
            r = client.post(
                "/v1/orgs",
                json={"slug": slug, "display_name": slug.title(), "is_private": priv},
                headers=_hdr(owner),
            )
            assert r.status_code in (200, 201), r.text
        yield client, admin, owner, stranger, reader


def _publish_private(client, token, name="libsecret", org="shell"):
    r = client.post(
        "/v1/publish",
        params={
            "name": name,
            "version": "1.0.0",
            "platform": "linux",
            "arch": "x86_64",
            "org": org,
        },
        files={"file": (f"{name}.tar.zst", b"secret-bytes", "application/octet-stream")},
        headers=_hdr(token),
    )
    assert r.status_code in (200, 201), r.text
    return r


# ── pure unit: XSS encoder ──────────────────────────────────────
class TestJsStringLiteralXss:
    def test_script_close_is_neutralised(self):
        from cvcpkg.server.landing import _js_string_literal

        out = _js_string_literal("</script><img src=x onerror=steal(localStorage)>")
        assert "<" not in out and ">" not in out
        assert "</script>" not in out
        assert "\\u003c" in out  # angle brackets survive only as escapes

    def test_ampersand_escaped(self):
        from cvcpkg.server.landing import _js_string_literal

        assert "&" not in _js_string_literal("a & b")


# ── pure unit: tar-slip ─────────────────────────────────────────
class TestTarSlipGuard:
    def test_detects_parent_escape(self):
        from cvcpkg._archive import tar_has_unsafe_member

        data = _tar_bytes([("../../etc/evil", b"x")])
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            assert tar_has_unsafe_member(tf) == "../../etc/evil"

    def test_extract_rejects_escape(self, tmp_path):
        from cvcpkg._archive import safe_tar_extractall

        data = _tar_bytes([("../escape.txt", b"x")])
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            with pytest.raises(ValueError):
                safe_tar_extractall(tf, dest)
        assert not (tmp_path / "escape.txt").exists()

    def test_extract_allows_safe(self, tmp_path):
        from cvcpkg._archive import safe_tar_extractall, tar_has_unsafe_member

        data = _tar_bytes([("pkg/ok.txt", b"hello")])
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            assert tar_has_unsafe_member(tf) is None
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            safe_tar_extractall(tf, dest)
        assert (dest / "pkg" / "ok.txt").read_text() == "hello"


# ── registration hardening ──────────────────────────────────────
class TestRegisterHardening:
    def test_open_registration_is_reader_only(self, sec_server):
        client, *_ = sec_server
        r = client.post(
            "/v1/register",
            json={"name": "climber", "email": "c@x.io", "role": "publisher"},
        )
        assert r.status_code in (200, 201), r.text
        raw = r.json()["token"]
        # A clamped reader token cannot create orgs (publisher|admin only).
        denied = client.post(
            "/v1/orgs",
            json={"slug": "evilcorp", "display_name": "Evil"},
            headers=_hdr(raw),
        )
        assert denied.status_code == 403

    def test_registration_is_rate_limited(self, sec_server, monkeypatch):
        client, *_ = sec_server
        monkeypatch.setattr(app_mod, "RATE_LIMIT_RPM", 3, raising=False)
        # Fresh IP bucket for this app; the fixture used a different code path,
        # but be robust: register until we either hit 429 or exhaust attempts.
        codes = []
        for i in range(6):
            r = client.post(
                "/v1/register",
                json={"name": f"flood{i}", "email": f"f{i}@x.io"},
            )
            codes.append(r.status_code)
        assert 429 in codes, codes


# ── mirror registration must be admin-only ──────────────────────
class TestMirrorRegisterAuth:
    def _body(self):
        return {"url": "http://attacker.example", "display_name": "evil"}

    def test_anonymous_rejected(self, sec_server):
        client, *_ = sec_server
        assert client.post("/v1/mirrors/register", json=self._body()).status_code in (401, 403)

    def test_reader_and_publisher_rejected(self, sec_server):
        client, _admin, _owner, stranger, reader = sec_server
        assert (
            client.post("/v1/mirrors/register", json=self._body(), headers=_hdr(reader)).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/mirrors/register", json=self._body(), headers=_hdr(stranger)
            ).status_code
            == 403
        )

    def test_admin_allowed(self, sec_server):
        client, admin, *_ = sec_server
        r = client.post("/v1/mirrors/register", json=self._body(), headers=_hdr(admin))
        assert r.status_code in (200, 201), r.text


# ── recipe namespace: traversal + cross-tenant isolation ────────
class TestRecipeNamespace:
    def _upload(self, client, token, org, name="librecipe", content=_GOOD_RECIPE):
        return client.post(
            f"/v1/recipes/{name}",
            params={"org_slug": org, "version": "1.0.0"},
            files={"file": (f"{name}.tar.gz", content, "application/gzip")},
            headers=_hdr(token),
        )

    def test_org_slug_traversal_rejected(self, sec_server):
        client, _admin, owner, *_ = sec_server
        r = self._upload(client, owner, "../../etc")
        assert r.status_code == 422

    def test_nonmember_upload_forbidden(self, sec_server):
        client, _admin, _owner, stranger, _reader = sec_server
        assert self._upload(client, stranger, "shell").status_code == 403

    def test_member_upload_ok_and_unsafe_bundle_rejected(self, sec_server):
        client, _admin, owner, *_ = sec_server
        assert self._upload(client, owner, "shell").status_code in (200, 201)
        evil = _tar_bytes([("../../evil.sh", b"rm -rf /")])
        r = self._upload(client, owner, "shell", name="libevil", content=evil)
        assert r.status_code == 400

    def test_recipe_set_bundle_private_hidden(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        assert self._upload(client, owner, "shell").status_code in (200, 201)
        assert client.get("/v1/recipes/bundle", params={"org_slug": "shell"}).status_code == 404
        assert (
            client.get(
                "/v1/recipes/bundle", params={"org_slug": "shell"}, headers=_hdr(stranger)
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/recipes/bundle", params={"org_slug": "shell"}, headers=_hdr(owner)
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/v1/recipes/bundle", params={"org_slug": "shell"}, headers=_hdr(admin)
            ).status_code
            == 200
        )

    def test_list_recipes_cross_tenant(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        assert self._upload(client, owner, "shell").status_code in (200, 201)
        # explicit private-org filter: forbidden to a non-member
        assert (
            client.get(
                "/v1/recipes", params={"org_slug": "shell"}, headers=_hdr(stranger)
            ).status_code
            == 403
        )
        # unscoped listing: the private recipe must not surface for a non-member
        strangers = client.get("/v1/recipes", headers=_hdr(stranger)).json()["recipes"]
        assert all(r["org_slug"] != "shell" for r in strangers)
        owners = client.get("/v1/recipes", headers=_hdr(owner)).json()["recipes"]
        assert any(r["org_slug"] == "shell" for r in owners)


# ── private-org package leaks ───────────────────────────────────
class TestPrivatePackageLeaks:
    def test_cache_hides_private(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        _publish_private(client, owner)
        stranger_names = {
            p["name"] for p in client.get("/v1/cache", headers=_hdr(stranger)).json()["packages"]
        }
        assert "libsecret" not in stranger_names
        owner_names = {
            p["name"] for p in client.get("/v1/cache", headers=_hdr(owner)).json()["packages"]
        }
        assert "libsecret" in owner_names
        admin_names = {
            p["name"] for p in client.get("/v1/cache", headers=_hdr(admin)).json()["packages"]
        }
        assert "libsecret" in admin_names

    def test_feed_hides_private(self, sec_server):
        client, _admin, owner, *_ = sec_server
        _publish_private(client, owner)
        assert "libsecret" not in client.get("/v1/feed.xml").text  # anonymous
        assert "libsecret" in client.get("/v1/feed.xml", headers=_hdr(owner)).text

    def test_archive_download_idor(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        _publish_private(client, owner)
        pkgs = client.get("/v1/packages", params={"name": "libsecret"}, headers=_hdr(owner)).json()
        bundles = pkgs.get("packages") or pkgs.get("bundles") or []
        assert bundles, pkgs
        url = bundles[0]["archive_url"]
        assert client.get(url, headers=_hdr(stranger)).status_code == 404
        assert client.get(url).status_code == 404  # anonymous
        assert client.get(url, headers=_hdr(owner)).status_code == 200
        assert client.get(url, headers=_hdr(admin)).status_code == 200


# ── build-job / build-log org isolation ─────────────────────────
class TestBuildIsolation:
    def _submit(self, client, token, org="shell"):
        return client.post(
            "/v1/builds",
            json={
                "recipe_name": "libsecret",
                "platform": "linux",
                "arch": "x86_64",
                "org_slug": org,
            },
            headers=_hdr(token),
        )

    def test_nonmember_cannot_submit_into_org(self, sec_server):
        client, _admin, _owner, stranger, _reader = sec_server
        assert self._submit(client, stranger).status_code == 403

    def test_get_build_scoped(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        r = self._submit(client, owner)
        assert r.status_code in (200, 201), r.text
        job_id = r.json()["id"]
        assert client.get(f"/v1/builds/{job_id}", headers=_hdr(stranger)).status_code == 404
        assert client.get(f"/v1/builds/{job_id}", headers=_hdr(owner)).status_code == 200
        assert client.get(f"/v1/builds/{job_id}", headers=_hdr(admin)).status_code == 200

    def test_list_builds_scoped(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        r = self._submit(client, owner)
        job_id = r.json()["id"]
        stranger_ids = {
            j["id"] for j in client.get("/v1/builds", headers=_hdr(stranger)).json()["jobs"]
        }
        assert job_id not in stranger_ids
        owner_ids = {j["id"] for j in client.get("/v1/builds", headers=_hdr(owner)).json()["jobs"]}
        assert job_id in owner_ids

    def test_build_log_access_control(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        r = self._submit(client, owner)
        job_id = r.json()["id"]
        # Non-member: the ACL fires before the log lookup -> "build job ... not found".
        denied = client.get(f"/v1/builds/{job_id}/log", headers=_hdr(stranger))
        assert denied.status_code == 404
        assert "build job" in denied.json()["detail"]
        # Owner: passes the ACL, then 404s only because no log has been produced.
        owner_resp = client.get(f"/v1/builds/{job_id}/log", headers=_hdr(owner))
        assert owner_resp.status_code == 404
        assert "no log" in owner_resp.json()["detail"]

    def test_cancel_build_scoped(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        r = self._submit(client, owner)
        job_id = r.json()["id"]
        assert client.post(f"/v1/builds/{job_id}/cancel", headers=_hdr(stranger)).status_code == 404


# ── second-round: adversarial-verification findings ─────────────
class TestTarSlipHardlink:
    def _evil_hardlink(self):
        # s/hl -> foo/../../victim : escapes the extraction ROOT (a subdir
        # hardlink under-counts the '..' vs a parent-relative check).
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for d in ("foo", "s"):
                ti = tarfile.TarInfo(d)
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tf.addfile(ti)
            hl = tarfile.TarInfo("s/hl")
            hl.type = tarfile.LNKTYPE
            hl.linkname = "foo/../../victim"
            tf.addfile(hl)
            payload = b"PWNED"
            reg = tarfile.TarInfo("s/hl")
            reg.size = len(payload)
            tf.addfile(reg, io.BytesIO(payload))
        buf.seek(0)
        return buf

    def test_subdir_hardlink_escape_detected(self):
        from cvcpkg._archive import tar_has_unsafe_member

        with tarfile.open(fileobj=self._evil_hardlink()) as tf:
            assert tar_has_unsafe_member(tf) == "s/hl"

    def test_subdir_hardlink_escape_blocked(self, tmp_path):
        from cvcpkg._archive import safe_tar_extractall

        victim = tmp_path / "victim"
        victim.write_text("ORIGINAL")
        dest = tmp_path / "extract"
        dest.mkdir()
        with tarfile.open(fileobj=self._evil_hardlink()) as tf:
            with pytest.raises(ValueError):
                safe_tar_extractall(tf, dest)
        assert victim.read_text() == "ORIGINAL"  # not overwritten


class TestTagOrgXss:
    def test_tag_and_org_pages_neutralise_script_close(self):
        from cvcpkg.server.landing import org_detail_html, tag_detail_html

        payload = "</script><script>steal()</script>"
        assert "</script><script>steal" not in tag_detail_html("zlib", payload)
        assert "</script><script>steal" not in org_detail_html(payload)


class TestReadAuthEnforcement:
    @pytest.fixture()
    def ra_server(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'ra.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _seed():
            init_db(db_url)
            await create_tables()
            reader = await DbTokenStore(tmp_path).create("r", TokenRole.reader)
            await dispose_engine()
            return reader

        reader = asyncio.run(_seed())
        app = create_app(state_dir=tmp_path, require_auth_for_reads=True)
        with TestClient(app) as client:
            yield client, reader

    def test_anonymous_reads_rejected(self, ra_server):
        client, reader = ra_server
        # optional_token now enforces require_auth_for_reads on data-read paths.
        assert client.get("/v1/packages").status_code == 401
        assert client.get("/v1/feed.xml").status_code == 401
        assert client.get("/v1/download/whatever.tar.zst").status_code == 401
        # a valid token is still accepted
        assert client.get("/v1/packages", headers=_hdr(reader)).status_code == 200

    @pytest.fixture()
    def ra_server_env_only(self, tmp_path, monkeypatch):
        """`cvcpkg server run --require-auth-reads` / docker-compose's
        REQUIRE_AUTH_READS only ever set CVCPKG_SERVER_REQUIRE_AUTH_READS in
        the environment — uvicorn's factory entrypoint then calls
        create_app() with no arguments, so the flag must reach the app via
        the module-level REQUIRE_AUTH_FOR_READS constant, not the
        require_auth_for_reads parameter."""
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'ra_env.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
        monkeypatch.setattr(app_mod, "REQUIRE_AUTH_FOR_READS", True)
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        async def _seed():
            init_db(db_url)
            await create_tables()
            reader = await DbTokenStore(tmp_path).create("r", TokenRole.reader)
            await dispose_engine()
            return reader

        reader = asyncio.run(_seed())
        # No require_auth_for_reads kwarg — this is exactly what
        # uvicorn.run("cvcpkg.server.app:create_app", factory=True) invokes.
        app = create_app(state_dir=tmp_path)
        with TestClient(app) as client:
            yield client, reader

    def test_env_var_alone_enforces_read_auth(self, ra_server_env_only):
        client, reader = ra_server_env_only
        assert client.get("/v1/packages").status_code == 401
        assert client.get("/v1/feed.xml").status_code == 401
        assert client.get("/v1/download/whatever.tar.zst").status_code == 401
        assert client.get("/v1/packages", headers=_hdr(reader)).status_code == 200


class TestPrivateDownloadHardening:
    def test_head_download_gated(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        _publish_private(client, owner)
        pkgs = client.get("/v1/packages", params={"name": "libsecret"}, headers=_hdr(owner)).json()
        url = (pkgs.get("packages") or pkgs.get("bundles"))[0]["archive_url"]
        assert client.head(url, headers=_hdr(stranger)).status_code == 404
        assert client.head(url).status_code == 404  # anonymous
        assert client.head(url, headers=_hdr(owner)).status_code == 200

    def test_download_idor_survives_yank(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        _publish_private(client, owner)
        pkgs = client.get("/v1/packages", params={"name": "libsecret"}, headers=_hdr(owner)).json()
        url = (pkgs.get("packages") or pkgs.get("bundles"))[0]["archive_url"]
        # yank the private package (its row flips to yanked but the file remains)
        y = client.post("/v1/packages/libsecret/1.0.0/yank", headers=_hdr(owner))
        assert y.status_code in (200, 201), y.text
        # exact archive->org lookup includes yanked rows, so the gate still holds
        assert client.get(url, headers=_hdr(stranger)).status_code == 404
        assert client.get(url).status_code == 404


class TestBuildCallbackIsolation:
    def _submit(self, client, token, org="shell"):
        return client.post(
            "/v1/builds",
            json={
                "recipe_name": "libsecret",
                "platform": "linux",
                "arch": "x86_64",
                "org_slug": org,
            },
            headers=_hdr(token),
        )

    def test_builder_callbacks_scoped_to_org(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        job_id = self._submit(client, owner).json()["id"]
        h = _hdr(stranger)
        assert (
            client.post(f"/v1/builds/{job_id}/claim", json={"builder_id": 1}, headers=h).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/builds/{job_id}/complete", json={"result_archive_url": "x"}, headers=h
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/builds/{job_id}/fail", json={"error_message": "x"}, headers=h
            ).status_code
            == 404
        )
        assert (
            client.patch(f"/v1/builds/{job_id}/log", json={"data": "x"}, headers=h).status_code
            == 404
        )

    def test_dag_id_path_traversal_rejected(self, sec_server):
        client, _admin, owner, *_ = sec_server
        r = client.post(
            "/v1/builds/dag",
            json={
                "jobs": [{"recipe_name": "x", "platform": "linux", "arch": "x86_64"}],
                "dag_id": "../../../tmp/pwn",
            },
            headers=_hdr(owner),
        )
        assert r.status_code == 422


class TestSingleRecipeBundleMembership:
    def test_nonmember_cannot_download_private_bundle(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        up = client.post(
            "/v1/recipes/librecipe",
            params={"org_slug": "shell", "version": "1.0.0"},
            files={"file": ("librecipe.tar.gz", _GOOD_RECIPE, "application/gzip")},
            headers=_hdr(owner),
        )
        assert up.status_code in (200, 201), up.text
        assert (
            client.get(
                "/v1/recipes/librecipe", params={"org_slug": "shell"}, headers=_hdr(stranger)
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/recipes/librecipe", params={"org_slug": "shell"}, headers=_hdr(owner)
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/v1/recipes/librecipe", params={"org_slug": "shell"}, headers=_hdr(admin)
            ).status_code
            == 200
        )


class TestStatsOracles:
    def test_cache_stats_org_map_redacted(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        _publish_private(client, owner)  # gives 'shell' some storage
        stranger_orgs = client.get("/v1/cache/stats", headers=_hdr(stranger)).json().get("orgs", {})
        assert "shell" not in stranger_orgs
        owner_orgs = client.get("/v1/cache/stats", headers=_hdr(owner)).json().get("orgs", {})
        assert "shell" in owner_orgs
        admin_orgs = client.get("/v1/cache/stats", headers=_hdr(admin)).json().get("orgs", {})
        assert "shell" in admin_orgs

    def test_download_stats_not_a_private_oracle(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        _publish_private(client, owner)
        pkgs = client.get("/v1/packages", params={"name": "libsecret"}, headers=_hdr(owner)).json()
        url = (pkgs.get("packages") or pkgs.get("bundles"))[0]["archive_url"]
        client.get(url, headers=_hdr(owner))  # record one download (synchronous)
        # non-member sees zeros for a private package they cannot see
        assert (
            client.get(
                "/v1/downloads/stats", params={"name": "libsecret"}, headers=_hdr(stranger)
            ).json()["total"]
            == 0
        )
        # a member is not over-blocked
        assert (
            client.get(
                "/v1/downloads/stats", params={"name": "libsecret"}, headers=_hdr(owner)
            ).json()["total"]
            >= 1
        )


# ── low-severity follow-ups folded in ──────────────────────────
class TestLowSeverityFindings:
    def test_list_builds_total_not_leaked(self, sec_server):
        client, _admin, owner, stranger, _reader = sec_server
        r = client.post(
            "/v1/builds",
            json={"recipe_name": "x", "platform": "linux", "arch": "x86_64", "org_slug": "shell"},
            headers=_hdr(owner),
        )
        assert r.status_code in (200, 201), r.text
        # count is filtered in the DB query, so a non-member sees total 0
        sres = client.get("/v1/builds", headers=_hdr(stranger)).json()
        assert sres["total"] == 0 and sres["jobs"] == []
        assert client.get("/v1/builds", headers=_hdr(owner)).json()["total"] >= 1

    def test_private_org_tags_gated(self, sec_server):
        client, admin, owner, stranger, _reader = sec_server
        # tag creation is admin-gated at the dependency level
        t = client.post(
            "/v1/tags", json={"name": "secret-tag", "org_slug": "shell"}, headers=_hdr(admin)
        )
        assert t.status_code in (200, 201), t.text
        assert (
            client.get("/v1/tags", params={"org": "shell"}, headers=_hdr(stranger)).status_code
            == 403
        )
        assert (
            client.get("/v1/tags", params={"org": "shell"}, headers=_hdr(owner)).status_code == 200
        )
        names = {x["name"] for x in client.get("/v1/tags", headers=_hdr(stranger)).json()["tags"]}
        assert "secret-tag" not in names  # unscoped listing must not leak it

    def test_webhook_ssrf_blocked(self, sec_server):
        client, admin, *_ = sec_server
        for bad in (
            "http://127.0.0.1:9/hook",
            "http://169.254.169.254/latest",
            "ftp://example.com/x",
        ):
            r = client.post(
                "/v1/webhooks",
                json={"url": bad, "events": ["package.published"]},
                headers=_hdr(admin),
            )
            assert r.status_code == 422, (bad, r.status_code)

    def test_org_logo_and_homepage_scheme_rejected(self, sec_server):
        client, _admin, owner, *_ = sec_server
        assert (
            client.post(
                "/v1/orgs",
                json={"slug": "jsorg", "display_name": "J", "logo_url": "javascript:alert(1)"},
                headers=_hdr(owner),
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/orgs",
                json={"slug": "jsorg2", "display_name": "J", "homepage": "javascript:alert(1)"},
                headers=_hdr(owner),
            ).status_code
            == 422
        )

    def test_telemetry_rate_limited(self, sec_server, monkeypatch):
        client, *_ = sec_server
        monkeypatch.setattr(app_mod, "RATE_LIMIT_RPM", 3, raising=False)
        codes = [
            client.post("/v1/telemetry", json={"platform": "linux"}).status_code for _ in range(6)
        ]
        assert 429 in codes, codes
