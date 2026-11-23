"""``/v1/search`` puts the best match first and pages whole packages.

Searching ``vtk`` on cvcpkg.org listed the packages whose descriptions mention
VTK first (alphabetically: ``cvc/ari`` ...) and ``vtk`` itself last, and the
search page paged 100 *bundle rows* at a time while counting *packages*: one
package with hundreds of builds filled a page alone, so "Load more" added one
package, or a few, apparently at random.  These pin the fix on both backends
(SQL and the file index a mirror serves from):

* packages rank exact name, then name prefix, name substring, tag, and last
  any other field (``cvcpkg.server.search_rank``);
* ``group=package`` pages count packages, so each page is full.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")

from fastapi.testclient import TestClient

from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole
from cvcpkg.server.search_rank import (
    RANK_EXACT,
    RANK_NAME,
    RANK_OTHER,
    RANK_PREFIX,
    RANK_TAG,
    relevance_rank,
    summarize_group,
)
from tests.unit._build_job_backends import BACKENDS, PG_URL, server_on


@pytest.fixture(params=["file", *BACKENDS])
def search_client(request, tmp_path, monkeypatch):
    """A test server and a publisher token, on the file index or a database.

    ``sqlite`` always runs; ``postgres`` (production's backend) runs when
    CVCPKG_TEST_POSTGRES_URL is set, as in CI's Postgres job.
    """
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    if request.param == "file":
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        from cvcpkg.server.auth import TokenStore

        token = TokenStore(tmp_path).create("test-publisher", TokenRole.publisher)
        with TestClient(create_app(state_dir=tmp_path)) as client:
            yield client, token
        return
    pytest.importorskip("aiosqlite", reason="aiosqlite required")
    with server_on(request.param, tmp_path, monkeypatch, {"pub": "publisher"}) as (client, raw):
        yield client, raw["pub"]


def _publish(client, token, name, version="1.0", platform="linux", arch="x86_64", **meta):
    params = {
        "name": name,
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": "release",
        "link": "shared",
        **meta,
    }
    body = f"{name}-{version}-{platform}-{arch}".encode()
    resp = client.post(
        "/v1/publish",
        params=params,
        files={"file": (f"{name}-{version}-{platform}-{arch}.tar.zst", io.BytesIO(body))},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


# Best match first for q=vtk: the exact name, a name prefix, a name substring, a
# tag, then the packages that only mention VTK (alphabetically).
VTK_ORDER = ["vtk", "vtk-python", "pyvtk", "cvcgl", "ari", "hdf5"]


def _seed(client, token):
    # ari: alphabetically first, many builds, and only its description says VTK.
    for v in ("1.0", "1.1", "1.2"):
        for plat in ("linux", "macos"):
            _publish(client, token, "ari", v, plat, description="Viewer built on VTK")
    _publish(client, token, "hdf5", "1.14", description="HDF5, as used by VTK")
    _publish(client, token, "cvcgl", "2.0", tags="graphics, vtk-bridge", description="GL")
    _publish(client, token, "cvcgl", "2.0", "windows", tags="graphics, vtk-bridge")
    _publish(client, token, "pyvtk", "0.5")
    _publish(client, token, "vtk-python", "9.4.1")
    _publish(client, token, "vtk", "9.3.0", "linux", "x86_64", description="Visualization")
    _publish(client, token, "vtk", "9.4.1", "macos", "arm64", description="Visualization")
    _publish(client, token, "zlib", "1.3.1", description="compression")


def _search(client, **params):
    resp = client.get("/v1/search", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _names(groups):
    return [g["name"] for g in groups]


class TestBestMatchFirst:
    def test_exact_name_leads_a_package_search(self, search_client):
        client, token = search_client
        _seed(client, token)
        data = _search(client, q="vtk", group="package")
        assert _names(data["groups"]) == VTK_ORDER
        assert data["group"] == "package"
        assert data["packages"] == []
        assert data["package_count"] == len(VTK_ORDER)
        assert data["total"] == 6 + 1 + 2 + 1 + 1 + 2

    def test_build_listing_ranks_packages_and_keeps_their_rows_together(self, search_client):
        client, token = search_client
        _seed(client, token)
        rows = _search(client, q="vtk", limit=200)["packages"]
        assert rows[0]["name"] == "vtk"
        runs = [
            n for i, n in enumerate(r["name"] for r in rows) if i == 0 or rows[i - 1]["name"] != n
        ]
        assert runs == VTK_ORDER  # each package once: its rows are contiguous

    def test_query_is_case_and_space_insensitive(self, search_client):
        client, token = search_client
        _seed(client, token)
        assert _names(_search(client, q="  VTK ", group="package")["groups"]) == VTK_ORDER

    def test_no_query_lists_packages_by_name(self, search_client):
        client, token = search_client
        _seed(client, token)
        data = _search(client, platform="linux", group="package")
        assert _names(data["groups"]) == [
            "ari",
            "cvcgl",
            "hdf5",
            "pyvtk",
            "vtk",
            "vtk-python",
            "zlib",
        ]


class TestPackagePaging:
    def test_every_page_holds_limit_packages(self, search_client):
        client, token = search_client
        _seed(client, token)
        seen = []
        for offset in range(0, len(VTK_ORDER), 2):
            page = _search(client, q="vtk", group="package", limit=2, offset=offset)
            assert len(page["groups"]) == 2, page["groups"]
            assert page["package_count"] == len(VTK_ORDER)
            seen += _names(page["groups"])
        assert seen == VTK_ORDER

    def test_past_the_end_and_limit_zero_keep_the_totals(self, search_client):
        client, token = search_client
        _seed(client, token)
        for params in ({"offset": 50}, {"limit": 0}):
            data = _search(client, q="vtk", group="package", **params)
            assert data["groups"] == []
            assert data["package_count"] == len(VTK_ORDER)
            assert data["total"] == 13

    def test_group_summarizes_the_package(self, search_client):
        client, token = search_client
        _seed(client, token)
        vtk = _search(client, q="vtk", group="package")["groups"][0]
        assert vtk["name"] == "vtk" and vtk["org"] == ""
        assert vtk["version"] == "9.4.1"  # newest
        assert vtk["platforms"] == ["linux", "macos"]
        assert vtk["archs"] == ["arm64", "x86_64"]
        assert vtk["builds"] == 2
        assert vtk["size_bytes"] == len(b"vtk-9.3.0-linux-x86_64") + len(b"vtk-9.4.1-macos-arm64")

    def test_tag_filter_matches_whole_tags(self, search_client):
        client, token = search_client
        _seed(client, token)
        assert _names(_search(client, tag="vtk-bridge", group="package")["groups"]) == ["cvcgl"]
        assert _names(_search(client, tag="Graphics", group="package")["groups"]) == ["cvcgl"]
        data = _search(client, tag="vtk", group="package")  # a substring of a tag, not a tag
        assert data["groups"] == [] and data["package_count"] == 0

    def test_live_filter_drops_released_builds(self, search_client):
        client, token = search_client
        _publish(client, token, "vtk", "9.3.0")
        _publish(client, token, "vtk", "9.4.1", "macos", release_tag="v9")
        _publish(client, token, "vtk-src", "9.4.1", release_tag="v9")
        data = _search(client, q="vtk", release="live", group="package")
        assert _names(data["groups"]) == ["vtk"]
        assert data["groups"][0]["builds"] == 1
        assert data["package_count"] == 1 and data["total"] == 1

    def test_unknown_group_is_rejected(self, search_client):
        client, _ = search_client
        assert client.get("/v1/search", params={"group": "nope"}).status_code == 422


class TestRankHelpers:
    @pytest.mark.parametrize(
        "name, tags, rank",
        [
            ("vtk", "", RANK_EXACT),
            ("VTK", "", RANK_EXACT),
            ("vtk-python", "", RANK_PREFIX),
            ("pyvtk", "", RANK_NAME),
            ("cvcgl", "graphics,vtk-bridge", RANK_TAG),
            ("ari", "", RANK_OTHER),
        ],
    )
    def test_relevance_rank(self, name, tags, rank):
        assert relevance_rank(name, tags, " vtk ") == rank

    def test_empty_query_ranks_everything_alike(self):
        assert relevance_rank("anything", "", "") == relevance_rank("vtk", "", "")

    def test_summarize_group_picks_the_newest_version(self):
        rows = [
            {"version": "9.10.0", "platform": "linux", "arch": "x86_64", "size_bytes": 5},
            {"version": "9.9.1", "platform": "windows", "arch": "x86_64", "size_bytes": 7},
        ]
        got = summarize_group("vtk", "", rows)
        assert got["version"] == "9.10.0"
        assert got["platforms"] == ["linux", "windows"]
        assert got["builds"] == 2 and got["size_bytes"] == 12


def _migration_039():
    """Migration 039's module (the search trigram index), loaded from its file."""
    versions = Path(__file__).resolve().parents[2] / "src" / "cvcpkg" / "migrations" / "versions"
    (path,) = versions.glob("*_039_*.py")
    spec = importlib.util.spec_from_file_location("_mig_039", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSearchIndex:
    """The free-text filter runs on Postgres from migration 039's trigram index,
    and a search's facets come from one query: what made ``/v1/search?q=`` take
    3-5 s on cvcpkg.org was 14 statements that each read every row."""

    def test_the_index_covers_the_expression_the_filter_uses(self):
        """The planner matches an index to a query by expression, so the
        migration's and the filter's must be the same, column for column."""
        from sqlalchemy.dialects import postgresql

        from cvcpkg.server.db_stores import _search_text

        compiled = str(_search_text().compile(dialect=postgresql.dialect()))
        assert compiled.replace("packages.", "") == _migration_039().SEARCH_TEXT

    def test_a_trailing_backslash_keeps_only_the_per_column_match(self):
        """A search ending in a backslash escapes LIKE's closing ``%`` and anchors
        the pattern at the end, where the joined text no longer follows from one
        column."""
        from cvcpkg.server.db_stores import _search_match

        assert len(_search_match("vtk")) == 2
        assert len(_search_match("vtk\\")) == 1

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_facets_count_packages_per_value(self, backend, tmp_path, monkeypatch):
        with server_on(backend, tmp_path, monkeypatch, {"pub": "publisher"}) as (client, raw):
            _seed(client, raw["pub"])
            data = _search(client, q="vtk")
            by_package = _search(client, q="vtk", group="package", limit=0)
        facets = {k: {b["value"]: b["count"] for b in v} for k, v in data["facets"].items()}
        assert facets["platforms"] == {"linux": 6, "macos": 2, "windows": 1}
        assert facets["archs"] == {"x86_64": 6, "arm64": 1}
        assert facets["links"] == {"shared": 6}
        assert facets["tags"] == {"graphics": 1, "vtk-bridge": 1}
        assert facets["releases"] == {} and facets["orgs"] == {}
        # Most packages first, then by value.
        assert [b["value"] for b in data["facets"]["platforms"]] == ["linux", "macos", "windows"]
        assert data["package_count"] == len(VTK_ORDER)
        # The build listing's totals (from the facet query) agree with the
        # package listing's (from its own grouped query).
        assert data["total"] == by_package["total"] == 13
        assert data["total_size_bytes"] == by_package["total_size_bytes"] > 0

    @pytest.mark.skipif(not PG_URL, reason="set CVCPKG_TEST_POSTGRES_URL to run against Postgres")
    def test_postgres_answers_the_filter_from_the_index(self, tmp_path, monkeypatch):
        """With 039 applied, the filter is an index scan and finds the same rows
        as the per-column ILIKEs alone, wildcards and all."""
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import func as sa_func
        from sqlalchemy import or_, select
        from sqlalchemy.ext.asyncio import create_async_engine

        from cvcpkg.server.db import PackageRow
        from cvcpkg.server.db_stores import _SEARCH_COLUMNS, _search_filters

        with server_on("postgres", tmp_path, monkeypatch, {"pub": "publisher"}) as (client, raw):
            _seed(client, raw["pub"])
        mig = _migration_039()

        def upgrade(sync_conn):
            with Operations.context(MigrationContext.configure(sync_conn)):
                mig.upgrade()

        def sql(dialect, stmt):
            return str(stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))

        def ids_sql(dialect, where):
            return sql(dialect, select(PackageRow.id).where(*where).order_by(PackageRow.id))

        async def check():
            engine = create_async_engine(PG_URL)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(upgrade)
                async with engine.begin() as conn:
                    indexes = await conn.exec_driver_sql(
                        "SELECT indexname FROM pg_indexes WHERE tablename = 'packages'"
                    )
                    assert mig.INDEX in indexes.scalars().all()
                    # A dozen rows: left alone, the planner scans, or walks the
                    # primary key for an ORDER BY id (CI's first run of this
                    # test).  With both off, a bitmap scan of the trigram
                    # index is the one way left to read packages, and the
                    # planner can take it only if the filter's expression is
                    # the indexed one.
                    await conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
                    await conn.exec_driver_sql("SET LOCAL enable_indexscan = off")
                    count = select(sa_func.count()).select_from(PackageRow)
                    filters = _search_filters(search="vtk", caller_token_name="")
                    plan = "\n".join(
                        r[0]
                        for r in await conn.exec_driver_sql(
                            "EXPLAIN " + sql(conn.dialect, count.where(*filters))
                        )
                    )
                    assert f"Bitmap Index Scan on {mig.INDEX}" in plan, plan
                    for q in ("vtk", "VTK", "graph", "9.4", "zz", "%", "_", "a_i", "vtk\\"):
                        like = f"%{q}%"
                        per_column = [or_(*(c.ilike(like) for c in _SEARCH_COLUMNS))]
                        got = await conn.exec_driver_sql(
                            ids_sql(conn.dialect, _search_filters(search=q, include_yanked=True))
                        )
                        want = await conn.exec_driver_sql(ids_sql(conn.dialect, per_column))
                        assert got.scalars().all() == want.scalars().all(), q
            finally:
                await engine.dispose()

        asyncio.run(check())
