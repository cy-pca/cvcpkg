"""Backfilling the runtime deps that pre-#152 catalog rows were published without.

Until #152 a server-cache push created catalog rows with ``required_deps: []``,
so the resolver installed those packages without their runtime dependencies.
``POST /v1/admin/packages/{name}/{version}/backfill-deps`` fills one variant's
EMPTY edge columns (never a populated one), and
``scripts/backfill_required_deps.py`` derives each row's edges from the recipe
that built that exact version, out of git history.
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import runtime_dependencies_from_raw

REPO = Path(__file__).resolve().parents[2]


# ── the admin endpoint (JSON-index server) ──────────────────────


@pytest.fixture()
def server_env(tmp_path):
    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.auth import TokenStore
    from cvcpkg.server.models import TokenRole

    store = TokenStore(tmp_path)
    admin = store.create("test-admin", TokenRole.admin)
    pub = store.create("test-publisher", TokenRole.publisher)
    with TestClient(create_app(state_dir=tmp_path)) as client:
        yield client, admin, pub


VARIANT = {"platform": "linux", "arch": "x86_64", "build_type": "release", "link": "shared"}
PATH = "/v1/admin/packages/libapp/1.0+cvc.1/backfill-deps"


def _publish(client, tok, **variant):
    params = {"name": "libapp", "version": "1.0+cvc.1", **VARIANT, **variant}
    resp = client.post(
        "/v1/publish",
        params=params,
        files={"file": ("libapp.tar.gz", io.BytesIO(b"data" + str(params).encode()))},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code in (200, 201), resp.text


def _rows(client):
    return {
        (b["platform"], b["arch"]): b
        for b in client.get("/v1/catalog").json()["bundles"]
        if b["name"] == "libapp"
    }


def _backfill(client, tok, body, **query):
    return client.post(
        PATH,
        params={**VARIANT, **query},
        json=body,
        headers={"Authorization": f"Bearer {tok}"},
    )


class TestEndpoint:
    def test_fills_only_the_named_variant(self, server_env):
        client, admin, pub = server_env
        _publish(client, pub)
        _publish(client, pub, platform="macos", arch="arm64")
        resp = _backfill(
            client, admin, {"required_deps": [{"name": "libfoo"}], "provides": ["app-slot"]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == {"required_deps": 1, "provides": 1}
        rows = _rows(client)
        assert rows[("linux", "x86_64")]["required_deps"] == [{"name": "libfoo"}]
        assert rows[("linux", "x86_64")]["provides"] == ["app-slot"]
        assert rows[("macos", "arm64")]["required_deps"] == []

    def test_never_rewrites_a_populated_column(self, server_env):
        client, admin, pub = server_env
        _publish(client, pub)
        assert _backfill(client, admin, {"required_deps": [{"name": "libfoo"}]}).status_code == 200
        resp = _backfill(client, admin, {"required_deps": [{"name": "other"}]})
        assert resp.status_code == 200
        assert resp.json()["count"]["required_deps"] == 0
        assert _rows(client)[("linux", "x86_64")]["required_deps"] == [{"name": "libfoo"}]

    def test_admin_only(self, server_env):
        client, admin, pub = server_env
        _publish(client, pub)
        assert _backfill(client, pub, {"required_deps": [{"name": "x"}]}).status_code == 403
        resp = client.post(PATH, params=VARIANT, json={"required_deps": [{"name": "x"}]})
        assert resp.status_code == 401

    def test_every_variant_key_is_required(self, server_env):
        client, admin, pub = server_env
        _publish(client, pub)
        for key in VARIANT:
            q = {k: v for k, v in VARIANT.items() if k != key}
            resp = client.post(
                PATH,
                params=q,
                json={"required_deps": [{"name": "x"}]},
                headers={"Authorization": f"Bearer {admin}"},
            )
            assert resp.status_code == 422, key

    def test_corrects_a_backfill_only_where_it_still_matches(self, server_env):
        # A backfill that wrote host tools (cmake/ninja) into a row that is
        # correctly empty is undone by compare-and-set: the row changes only
        # while it records exactly what that backfill wrote.
        client, admin, pub = server_env
        _publish(client, pub)
        _publish(client, pub, platform="macos", arch="arm64")
        wrong = [{"name": "cmake"}, {"name": "ninja"}]
        assert _backfill(client, admin, {"required_deps": wrong}).status_code == 200
        assert (
            _backfill(
                client, admin, {"required_deps": [{"name": "kept"}]}, platform="macos", arch="arm64"
            ).status_code
            == 200
        )

        stale = _backfill(
            client, admin, {"required_deps": [], "expect_required_deps": [{"name": "cmake"}]}
        )
        assert stale.json()["count"]["required_deps"] == 0
        assert _rows(client)[("linux", "x86_64")]["required_deps"] == wrong

        resp = _backfill(client, admin, {"required_deps": [], "expect_required_deps": wrong})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == {"required_deps": 1, "provides": 0}
        rows = _rows(client)
        assert rows[("linux", "x86_64")]["required_deps"] == []
        assert rows[("macos", "arm64")]["required_deps"] == [{"name": "kept"}]

    def test_correction_entries_are_validated(self, server_env):
        client, admin, pub = server_env
        _publish(client, pub)
        resp = _backfill(client, admin, {"required_deps": [], "expect_required_deps": [{"n": "x"}]})
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [
            {"required_deps": [{"nam": "x"}]},
            {"required_deps": [{"name": ""}]},
            {"required_deps": [{"name": "x", "platforms": "linux"}]},
            {"provides": [""]},
        ],
    )
    def test_rejects_malformed_edges(self, server_env, body):
        client, admin, pub = server_env
        _publish(client, pub)
        assert _backfill(client, admin, body).status_code == 422
        assert _rows(client)[("linux", "x86_64")]["required_deps"] == []


# ── the DB store ────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    from cvcpkg.server.db import create_tables, dispose_engine, init_db

    async def _init():
        init_db(db_url)
        await create_tables()

    asyncio.run(_init())
    try:
        from cvcpkg.server.db_stores import DbPackageIndex

        yield DbPackageIndex()
    finally:
        asyncio.run(dispose_engine())


def _add(idx, **kw):
    base = dict(
        name="libapp",
        version="1.0+cvc.1",
        sha256="ab" * 32,
        size_bytes=1,
        archive_url="/a.tar.gz",
        **VARIANT,
    )
    base.update(kw)
    asyncio.run(idx.add_package(**base))


class TestStore:
    def test_fills_empty_columns_of_one_variant(self, db):
        _add(db)
        _add(db, platform="macos", arch="arm64", archive_url="/b.tar.gz")
        # Another org's namespace never matches the public row.
        other_org = asyncio.run(
            db.backfill_deps(
                "libapp",
                "1.0+cvc.1",
                org_slug="team",
                required_deps='[{"name": "wrong"}]',
                provides="[]",
                **VARIANT,
            )
        )
        assert other_org == {"required_deps": 0, "provides": 0}
        counts = asyncio.run(
            db.backfill_deps(
                "libapp",
                "1.0+cvc.1",
                org_slug="",
                required_deps='[{"name": "libfoo"}]',
                provides='["slot"]',
                **VARIANT,
            )
        )
        assert counts == {"required_deps": 1, "provides": 1}
        rows = {(b["platform"], b["org"]): b for b in asyncio.run(db.get_catalog_dict())["bundles"]}
        assert rows[("linux", "")]["required_deps"] == [{"name": "libfoo"}]
        assert rows[("linux", "")]["provides"] == ["slot"]
        assert rows[("macos", "")]["required_deps"] == []

    def test_leaves_populated_columns_alone(self, db):
        _add(db, required_deps='[{"name": "kept"}]')
        counts = asyncio.run(
            db.backfill_deps(
                "libapp",
                "1.0+cvc.1",
                org_slug="",
                required_deps='[{"name": "libfoo"}]',
                provides="[]",
                **VARIANT,
            )
        )
        assert counts == {"required_deps": 0, "provides": 0}
        (row,) = asyncio.run(db.get_catalog_dict())["bundles"]
        assert row["required_deps"] == [{"name": "kept"}]

    def test_compare_and_set_replaces_only_an_exact_match(self, db):
        _add(db, required_deps='[{"name": "cmake"}, {"name": "ninja"}]')
        _add(
            db,
            platform="macos",
            arch="arm64",
            archive_url="/b.tar.gz",
            required_deps='[{"name": "cmake"}]',
        )

        def correct(**variant):
            return asyncio.run(
                db.backfill_deps(
                    "libapp",
                    "1.0+cvc.1",
                    org_slug="",
                    required_deps="[]",
                    provides="[]",
                    expect_required_deps='[{"name": "cmake"}, {"name": "ninja"}]',
                    **{**VARIANT, **variant},
                )
            )

        assert correct() == {"required_deps": 1, "provides": 0}
        assert correct(platform="macos", arch="arm64") == {"required_deps": 0, "provides": 0}
        rows = {b["platform"]: b for b in asyncio.run(db.get_catalog_dict())["bundles"]}
        assert rows["linux"]["required_deps"] == []
        assert rows["macos"]["required_deps"] == [{"name": "cmake"}]


# ── scripts/backfill_required_deps.py ───────────────────────────


@pytest.fixture
def script():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import backfill_required_deps

        yield backfill_required_deps
    finally:
        sys.path.remove(str(REPO / "scripts"))


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def _recipe(cvc, runtime, provides=()):
    return {
        "schema_version": 1,
        "recipe": {"name": "libapp", "upstream_version": "1.0", "cvc_revision": cvc},
        "provides": list(provides),
        "depends": {"runtime": runtime},
    }


@pytest.fixture
def history_repo(tmp_path):
    """recipes/libapp at cvc.1 (libfoo), cvc.2 (libfoo + linux-only libbar)."""
    repo = tmp_path / "repo"
    rdir = repo / "recipes" / "libapp"
    rdir.mkdir(parents=True)
    _git(repo, "init", "-q")
    (rdir / "recipe.yaml").write_text(yaml.safe_dump(_recipe(1, ["libfoo"])))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cvc.1")
    (rdir / "recipe.yaml").write_text(
        yaml.safe_dump(
            _recipe(2, ["libfoo", {"name": "libbar", "platforms": ["linux"]}], ["app-slot"])
        )
    )
    _git(repo, "commit", "-qam", "cvc.2")
    return repo


def _row(version, platform="linux", **kw):
    row = {
        "name": "libapp",
        "version": version,
        "platform": platform,
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "org": "",
        "required_deps": [],
        "provides": [],
    }
    row.update(kw)
    return row


class TestScript:
    def test_recipe_at_each_version(self, script, history_repo):
        h = script.RecipeHistory(history_repo)
        src1, raw1 = h.raw("libapp", "1.0+cvc.1")
        src2, raw2 = h.raw("libapp", "1.0+cvc.2")
        assert src2 == "working-tree" and src1 != "working-tree"
        assert raw1["depends"]["runtime"] == ["libfoo"]
        assert h.raw("libapp", "1.0+cvc.3") is None
        assert h.raw("nosuch", "1.0+cvc.1") is None

    def test_plan_uses_the_version_s_own_recipe(self, script, history_repo):
        rows = [
            _row("1.0+cvc.1"),
            _row("1.0+cvc.2"),
            _row("1.0+cvc.2", platform="macos"),
            _row("1.0+cvc.3"),
            _row("1.0+cvc.2", platform="freebsd", required_deps=[{"name": "kept"}], provides=["x"]),
            _row("1.0+cvc.2", platform="netbsd", yanked=True),
        ]
        fills = script.plan(rows, script.RecipeHistory(history_repo))
        got = {(f.row["version"], f.row["platform"]): f for f in fills}
        assert got[("1.0+cvc.1", "linux")].required_deps == [{"name": "libfoo"}]
        assert got[("1.0+cvc.1", "linux")].provides == []
        assert got[("1.0+cvc.2", "linux")].required_deps == [{"name": "libfoo"}, {"name": "libbar"}]
        assert got[("1.0+cvc.2", "linux")].provides == ["app-slot"]
        # libbar is linux-only, exactly as at publish (runtime_dependencies_from_raw).
        assert got[("1.0+cvc.2", "macos")].required_deps == [{"name": "libfoo"}]
        assert got[("1.0+cvc.3", "linux")].status == "no-recipe-at-version"
        assert ("1.0+cvc.2", "freebsd") not in got  # nothing empty to fill
        assert ("1.0+cvc.2", "netbsd") not in got  # yanked

    def test_apply_posts_only_fills(self, script, history_repo):
        rows = [_row("1.0+cvc.1"), _row("1.0+cvc.3")]
        fills = script.plan(rows, script.RecipeHistory(history_repo))
        posted = []

        def post(path, query, body):
            posted.append((path, query, body))
            return {"count": {"required_deps": 1, "provides": 0}}

        changed, errors = script.apply(fills, post)
        assert (changed, errors) == (1, [])
        ((path, query, body),) = posted
        assert path == "/v1/admin/packages/libapp/1.0%2Bcvc.1/backfill-deps"
        assert query == {**VARIANT, "org": ""}
        assert body == {"required_deps": [{"name": "libfoo"}], "provides": []}

    def test_apply_reports_errors_and_carries_on(self, script, history_repo):
        fills = script.plan(
            [_row("1.0+cvc.1"), _row("1.0+cvc.2")], script.RecipeHistory(history_repo)
        )

        def post(path, query, body):
            if "cvc.1" in path:
                raise RuntimeError("boom")
            return {"count": {"required_deps": 1, "provides": 1}}

        changed, errors = script.apply(fills, post)
        assert changed == 1 and len(errors) == 1 and "boom" in errors[0]

    def test_derivation_matches_the_publish_path(self):
        # The script must derive exactly what publish records, so it calls the
        # same helper the publish paths' runtime_dependencies() delegates to.
        raw = _recipe(2, ["a", {"name": "b", "platforms": ["macos"]}, {"name": "c", "org": "o"}])
        assert runtime_dependencies_from_raw(raw, "linux") == [
            {"name": "a"},
            {"name": "c", "org": "o"},
        ]
