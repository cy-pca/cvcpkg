"""``/v1/search?sort=`` orders every match by one key before the page is cut.

cypkg's search table sorts by a column header and pages 10 rows at a time, so
the sort must reorder the whole result, not the page on screen.  These pin the
contract on every backend /v1/search serves from: the database (SQLite here,
Postgres when CVCPKG_TEST_POSTGRES_URL is set), the file index, and a mirror's
sync-maintained index.

* each key, both directions, for build rows and for package summaries;
* ``-`` reverses the key, and rows that tie on it keep the default order;
* paging under a sort neither repeats nor skips a row;
* versions sort in version order (``1.10`` after ``1.9``, ``+cvc.10`` after
  ``+cvc.2``, a pre-release before its release);
* an unknown key, or one the group lacks, is a 422 that names the keys;
* facets and totals do not depend on the sort.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
yaml = pytest.importorskip("yaml")

from fastapi.testclient import TestClient

from cvcpkg.server.search_sort import (
    BUILD_SORT_KEYS,
    PACKAGE_SORT_KEYS,
    SearchSort,
    parse_sort,
    published_sort_key,
    sort_bundles,
)
from tests.unit._build_job_backends import BACKENDS, backend_url, fresh_schema

# (name, org, version, platform, arch, build_type, link, size_bytes, published day)
# Chosen so a text or lexical order gets versions wrong (1.10 < 1.9, cvc.10 <
# cvc.2, 10.0 < 3.1), one name is capitalised, two orgs share a package name,
# and every key has ties.
ROWS = [
    ("alpha", "", "1.9", "linux", "x86_64", "release", "shared", 300, 5),
    ("alpha", "", "1.10", "linux", "x86_64", "release", "shared", 100, 7),
    ("alpha", "", "1.10", "macos", "arm64", "debug", "static", 500, 6),
    ("Beta", "", "2.0.0-rc.1", "windows", "x86_64", "release", "shared", 50, 2),
    ("Beta", "", "2.0.0", "linux", "aarch64", "release", "static", 700, 3),
    ("gamma", "acme", "0.9+cvc.2", "linux", "x86_64", "release", "shared", 200, 1),
    ("gamma", "acme", "0.9+cvc.10", "freebsd", "x86_64", "release", "shared", 250, 4),
    ("delta", "zeta-org", "10.0", "wasm", "wasm32", "release", "static", 900, 8),
    ("alpha", "acme", "3.1", "linux", "x86_64", "release", "shared", 150, 9),
]

VERSIONS_ASCENDING = [
    "0.9+cvc.2",
    "0.9+cvc.10",
    "1.9",
    "1.10",
    "1.10",
    "2.0.0-rc.1",
    "2.0.0",
    "3.1",
    "10.0",
]

# Each package's newest version, oldest package first.
PACKAGES_BY_VERSION = [
    ("acme", "gamma"),  # 0.9+cvc.10
    ("", "alpha"),  # 1.10
    ("", "Beta"),  # 2.0.0
    ("acme", "alpha"),  # 3.1
    ("zeta-org", "delta"),  # 10.0
]


def _published(day: int) -> datetime.datetime:
    return datetime.datetime(2026, 1, day, 12, 0, tzinfo=datetime.timezone.utc)


def _bundle(row) -> dict:
    name, org, version, plat, arch, build_type, link, size, day = row
    return {
        "name": name,
        "org": org,
        "version": version,
        "platform": plat,
        "arch": arch,
        "build_type": build_type,
        "link": link,
        "size_bytes": size,
        "sha256": hashlib.sha256(repr(row).encode()).hexdigest(),
        "archive_url": f"/v1/download/{name}-{version}-{plat}-{arch}-{build_type}-{link}.tar.zst",
        "published_at": _published(day).isoformat(),
        "description": f"{name} for tests",
        "tags": "",
    }


def _write_index(tmp_path) -> None:
    (tmp_path / "index.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "revision": 1, "bundles": [_bundle(r) for r in ROWS]},
        )
    )


async def _seed_db(url: str) -> None:
    from cvcpkg.server.db import OrganizationRow, PackageRow, dispose_engine, get_session

    await fresh_schema(url)
    async with get_session() as session:
        for slug in ("acme", "zeta-org"):
            session.add(OrganizationRow(slug=slug, display_name=slug, created_by="test"))
        for row in ROWS:
            b = _bundle(row)
            session.add(
                PackageRow(
                    name=b["name"],
                    org_slug=b["org"],
                    version=b["version"],
                    platform=b["platform"],
                    arch=b["arch"],
                    build_type=b["build_type"],
                    link=b["link"],
                    size_bytes=b["size_bytes"],
                    sha256=b["sha256"],
                    archive_url=b["archive_url"],
                    published_at=_published(row[-1]),
                    description=b["description"],
                )
            )
    await dispose_engine()


@pytest.fixture(scope="module", params=["file", "mirror", *BACKENDS])
def client(request, tmp_path_factory):
    """A server holding ROWS: on the file index, a mirror's index, or a database.

    One server per backend for the whole module: every test here only reads.
    """
    import cvcpkg.server.app as app_mod

    tmp_path = tmp_path_factory.mktemp(f"sort-{request.param}")
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("CVCPKG_MIRROR_MODE", raising=False)
        mp.delenv("CVCPKG_POPULATE_UPSTREAM", raising=False)
        mp.setattr(app_mod, "_SCHEDULER_INTERVAL", 3600)
        if request.param == "file":
            mp.delenv("CVCPKG_DATABASE_URL", raising=False)
            _write_index(tmp_path)
        elif request.param == "mirror":
            # A mirror has a database, but serves reads from the synced index.
            pytest.importorskip("aiosqlite", reason="aiosqlite required")
            url = backend_url("sqlite", tmp_path)
            mp.setenv("CVCPKG_DATABASE_URL", url)
            mp.setenv("CVCPKG_MIRROR_MODE", "1")
            mp.setenv("CVCPKG_MIRROR_UPSTREAM", "https://primary.example.com")
            mp.setattr(app_mod, "MIRROR_MODE", True)
            mp.setattr(app_mod, "MIRROR_UPSTREAM", "https://primary.example.com")

            async def _empty():
                from cvcpkg.server.db import dispose_engine

                await fresh_schema(url)
                await dispose_engine()

            asyncio.run(_empty())
            _write_index(tmp_path)
        else:
            pytest.importorskip("aiosqlite", reason="aiosqlite required")
            url = backend_url(request.param, tmp_path)
            mp.setenv("CVCPKG_DATABASE_URL", url)
            asyncio.run(_seed_db(url))
        with TestClient(app_mod.create_app(state_dir=tmp_path)) as c:
            yield c


def _search(client, **params) -> dict:
    params.setdefault("limit", 200)
    resp = client.get("/v1/search", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _when(text: str) -> datetime.datetime:
    return published_sort_key(text)


# What each key compares in a response row, written out independently of the
# server's code: text case-insensitively, numbers and times as such.
BUILD_VALUE = {
    "name": lambda r: r["name"].lower(),
    "platform": lambda r: r["platform"].lower(),
    "arch": lambda r: r["arch"].lower(),
    "build_type": lambda r: r["build_type"].lower(),
    "link": lambda r: r["link"].lower(),
    "size": lambda r: r["size_bytes"],
    "published": lambda r: _when(r["published_at"]),
    "org": lambda r: r["org"].lower(),
}
GROUP_VALUE = {
    "name": lambda g: g["name"].lower(),
    "org": lambda g: g["org"].lower(),
    "builds": lambda g: g["builds"],
    "size": lambda g: g["size_bytes"],
}


def _build_id(r: dict) -> tuple:
    return (r["org"], r["name"], r["version"], r["platform"], r["arch"], r["build_type"], r["link"])


def _group_id(g: dict) -> tuple:
    return (g["org"], g["name"])


def _ties_keep_their_order(items, value, ident) -> dict:
    """Each run of equal *value* as the list of its items' ids, in order."""
    runs: dict = {}
    for item in items:
        runs.setdefault(value(item), []).append(ident(item))
    return runs


class TestBuildRows:
    @pytest.mark.parametrize("key", [k for k in BUILD_SORT_KEYS if k != "version"])
    @pytest.mark.parametrize("descending", [False, True])
    def test_each_key_orders_every_row(self, client, key, descending):
        data = _search(client, sort=("-" if descending else "") + key)
        rows = data["packages"]
        assert len(rows) == len(ROWS)
        values = [BUILD_VALUE[key](r) for r in rows]
        assert values == sorted(values, reverse=descending)
        assert data["sort"] == ("-" if descending else "") + key

    def test_version_sorts_in_version_order(self, client):
        rows = _search(client, sort="version")["packages"]
        assert [r["version"] for r in rows] == VERSIONS_ASCENDING
        rows = _search(client, sort="-version")["packages"]
        assert [r["version"] for r in rows] == VERSIONS_ASCENDING[::-1]

    def test_name_is_case_insensitive(self, client):
        names = [r["name"] for r in _search(client, sort="name")["packages"]]
        assert names.index("Beta") > names.index("alpha")
        assert names.index("Beta") < names.index("delta")

    @pytest.mark.parametrize("key", BUILD_SORT_KEYS)
    def test_minus_reverses_the_key_and_ties_keep_their_order(self, client, key):
        value = BUILD_VALUE.get(key, lambda r: r["version"])
        up = _search(client, sort=key)["packages"]
        down = _search(client, sort="-" + key)["packages"]
        assert [value(r) for r in down] == [value(r) for r in up][::-1]
        # A run of rows equal on the key comes in the same order both ways.
        assert _ties_keep_their_order(up, value, _build_id) == _ties_keep_their_order(
            down, value, _build_id
        )

    @pytest.mark.parametrize("key", BUILD_SORT_KEYS)
    @pytest.mark.parametrize("descending", [False, True])
    def test_paging_never_repeats_or_skips(self, client, key, descending):
        sort = ("-" if descending else "") + key
        whole = [_build_id(r) for r in _search(client, sort=sort)["packages"]]
        paged = []
        for offset in range(0, len(ROWS) + 2, 2):
            page = _search(client, sort=sort, limit=2, offset=offset)
            assert page["total"] == len(ROWS)
            paged += [_build_id(r) for r in page["packages"]]
        assert paged == whole
        assert len(set(paged)) == len(ROWS)

    def test_a_query_still_filters_under_a_sort(self, client):
        rows = _search(client, q="alpha", sort="-size")["packages"]
        assert {r["name"] for r in rows} == {"alpha"}
        assert [r["size_bytes"] for r in rows] == [500, 300, 150, 100]

    def test_filters_and_limit_zero_under_a_sort(self, client):
        rows = _search(client, platform="linux", sort="-version")["packages"]
        assert [r["version"] for r in rows] == ["3.1", "2.0.0", "1.10", "1.9", "0.9+cvc.2"]
        data = _search(client, sort="version", limit=0)
        assert data["packages"] == [] and data["total"] == len(ROWS)


class TestPackageGroups:
    @pytest.mark.parametrize("key", [k for k in PACKAGE_SORT_KEYS if k != "version"])
    @pytest.mark.parametrize("descending", [False, True])
    def test_each_key_orders_every_package(self, client, key, descending):
        data = _search(client, group="package", sort=("-" if descending else "") + key)
        groups = data["groups"]
        assert len(groups) == 5
        values = [GROUP_VALUE[key](g) for g in groups]
        assert values == sorted(values, reverse=descending)

    def test_version_is_the_newest_matching_one_in_version_order(self, client):
        groups = _search(client, group="package", sort="version")["groups"]
        assert [_group_id(g) for g in groups] == PACKAGES_BY_VERSION
        assert [g["version"] for g in groups] == ["0.9+cvc.10", "1.10", "2.0.0", "3.1", "10.0"]
        groups = _search(client, group="package", sort="-version")["groups"]
        assert [_group_id(g) for g in groups] == PACKAGES_BY_VERSION[::-1]

    def test_version_counts_only_matching_builds(self, client):
        # Beta's only windows build is its release candidate, and gamma's only
        # linux one is cvc.2: a package sorts by its newest *matching* version.
        groups = _search(client, group="package", platform="windows", sort="version")["groups"]
        assert [(g["name"], g["version"]) for g in groups] == [("Beta", "2.0.0-rc.1")]
        groups = _search(client, group="package", platform="linux", sort="-version")["groups"]
        assert [(g["org"], g["name"], g["version"]) for g in groups] == [
            ("acme", "alpha", "3.1"),
            ("", "Beta", "2.0.0"),
            ("", "alpha", "1.10"),
            ("acme", "gamma", "0.9+cvc.2"),
        ]

    def test_ties_keep_best_match_first(self, client):
        # Every package matches "a" (release, x86_64, ...); alpha ranks first
        # (its name starts with "a"), then the names containing it, by name.
        up = _search(client, q="a", group="package", sort="builds")["groups"]
        assert [_group_id(g) for g in up] == [
            ("acme", "alpha"),  # 1 build
            ("zeta-org", "delta"),  # 1
            ("", "Beta"),  # 2
            ("acme", "gamma"),  # 2
            ("", "alpha"),  # 3
        ]
        down = _search(client, q="a", group="package", sort="-builds")["groups"]
        assert [_group_id(g) for g in down] == [
            ("", "alpha"),
            ("", "Beta"),
            ("acme", "gamma"),
            ("acme", "alpha"),
            ("zeta-org", "delta"),
        ]

    @pytest.mark.parametrize("key", PACKAGE_SORT_KEYS)
    @pytest.mark.parametrize("descending", [False, True])
    def test_paging_never_repeats_or_skips(self, client, key, descending):
        sort = ("-" if descending else "") + key
        whole = [_group_id(g) for g in _search(client, group="package", sort=sort)["groups"]]
        paged = []
        for offset in range(0, 7, 2):
            page = _search(client, group="package", sort=sort, limit=2, offset=offset)
            assert page["package_count"] == 5
            paged += [_group_id(g) for g in page["groups"]]
        assert paged == whole
        assert len(set(paged)) == 5


class TestContract:
    def test_no_sort_is_the_default_order(self, client):
        for group in ("build", "package"):
            plain = _search(client, q="alpha", group=group)
            empty = _search(client, q="alpha", group=group, sort="")
            assert plain == empty
            assert plain["sort"] == ""

    def test_facets_and_totals_do_not_change(self, client):
        for group in ("build", "package"):
            plain = _search(client, group=group, q="a")
            for sort in ("-size", "version", "-name"):
                sorted_ = _search(client, group=group, q="a", sort=sort)
                for field in ("total", "package_count", "total_size_bytes", "facets"):
                    assert sorted_[field] == plain[field], (group, sort, field)

    @pytest.mark.parametrize("bad", ["bogus", "-bogus", "-", "--name", "size_bytes"])
    def test_unknown_key_is_a_422_naming_the_keys(self, client, bad):
        resp = client.get("/v1/search", params={"sort": bad})
        assert resp.status_code == 422, resp.text
        (err,) = resp.json()["detail"]
        assert err["loc"] == ["query", "sort"]
        for key in BUILD_SORT_KEYS + PACKAGE_SORT_KEYS:
            assert key in err["msg"]

    @pytest.mark.parametrize("key", ["platform", "-arch", "published", "link", "build_type"])
    def test_a_build_key_is_a_422_for_packages(self, client, key):
        resp = client.get("/v1/search", params={"sort": key, "group": "package"})
        assert resp.status_code == 422, resp.text
        (err,) = resp.json()["detail"]
        assert err["loc"] == ["query", "sort"]
        assert "group=package" in err["msg"]
        for allowed in PACKAGE_SORT_KEYS:
            assert allowed in err["msg"]

    def test_builds_is_a_422_for_build_rows(self, client):
        resp = client.get("/v1/search", params={"sort": "builds"})
        assert resp.status_code == 422
        assert "group=build" in resp.json()["detail"][0]["msg"]

    def test_openapi_describes_the_parameter(self, client):
        params = client.get("/openapi.json").json()["paths"]["/v1/search"]["get"]["parameters"]
        sort = next(p for p in params if p["name"] == "sort")
        assert sort["schema"]["default"] == ""
        for key in BUILD_SORT_KEYS + PACKAGE_SORT_KEYS:
            assert key in sort["description"]


class TestHelpers:
    @pytest.mark.parametrize(
        "raw, want",
        [
            ("", None),
            ("  ", None),
            ("name", SearchSort("name", False)),
            ("-size", SearchSort("size", True)),
            (" -published ", SearchSort("published", True)),
        ],
    )
    def test_parse_sort(self, raw, want):
        assert parse_sort(raw) == want

    def test_sort_round_trips_as_text(self):
        assert str(SearchSort("size", True)) == "-size"
        assert str(SearchSort("name")) == "name"

    @pytest.mark.parametrize(
        "value",
        [
            "2026-01-05T12:00:00+00:00",
            "2026-01-05T12:00:00Z",
            "2026-01-05T07:00:00-05:00",
            "2026-01-05T12:00:00",
            datetime.datetime(2026, 1, 5, 12, 0),
            datetime.datetime(2026, 1, 5, 12, 0, tzinfo=datetime.timezone.utc),
        ],
    )
    def test_published_reads_every_form_the_index_holds(self, value):
        assert published_sort_key(value) == _published(5)

    @pytest.mark.parametrize("value", [None, "", "yesterday", 17])
    def test_unreadable_published_is_the_epoch(self, value):
        assert published_sort_key(value).year == 1970

    def test_sort_bundles_is_stable_both_ways(self):
        bundles = [{"name": "a", "n": i, "size_bytes": i % 2} for i in range(6)]
        up = sort_bundles(bundles, SearchSort("size"))
        down = sort_bundles(bundles, SearchSort("size", True))
        assert [b["n"] for b in up] == [0, 2, 4, 1, 3, 5]
        assert [b["n"] for b in down] == [1, 3, 5, 0, 2, 4]
