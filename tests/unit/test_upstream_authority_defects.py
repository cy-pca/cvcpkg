"""Regressions for defects found auditing the upstream-authoritative feature.

Each test here corresponds to a specific way the first implementation failed.
The headline one is laundering: the feature's whole promise is that a bundle
withdrawn upstream cannot come back just because one mirror still serves it, and
the original classifier read only ``yanked`` -- so a *dissenting middle hop*
reported the bundle as ordinary-present and every server below it un-yanked and
forgot.  The existing chain tests all dissented at the leaf, which has nothing
downstream, so the laundering hop was never exercised.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

UPSTREAM = "https://cvcpkg.org"


def _bundle(name, *, yanked=False, upstream_yanked=False):
    return {
        "name": name,
        "version": "1.0.0+cvc.1",
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "yanked": yanked,
        "upstream_yanked": upstream_yanked,
    }


def _key(name):
    return (name, "1.0.0+cvc.1", "linux", "x86_64", "release", "shared")


class TestDissentDoesNotLaunder:
    """A mid-chain mirror's dissent must not erase the origin's verdict."""

    def _capture(self, monkeypatch, bundles):
        """Run _reconcile_with_upstream and capture what it classified."""
        from cvcpkg.server import app as app_mod

        seen: dict = {}

        class _Pkgs:
            async def mirrored_keys(self, upstream):
                return {_key(b["name"]) for b in bundles}

            async def reconcile_from_upstream(self, upstream, **kw):
                seen.update(kw)
                return {
                    "yanked": 0,
                    "tombstoned": 0,
                    "ambiguous": 0,
                    "overridden": 0,
                    "unyanked": 0,
                }

        class _Client:
            async def get(self, url):  # no bundle goes missing in these cases
                raise AssertionError(f"unexpected tombstone fetch: {url}")

        monkeypatch.setattr(app_mod, "_db_packages", _Pkgs())
        asyncio.run(app_mod._reconcile_with_upstream(_Client(), UPSTREAM, bundles))
        return seen

    def test_upstream_yanked_from_a_dissenting_hop_is_treated_as_yanked(self, monkeypatch):
        # The dissenting mirror serves it (yanked=False) but discloses that its
        # own upstream retired it.  Reading only `yanked` would file this under
        # "present" and un-yank it one hop down -- laundering the origin's call.
        seen = self._capture(monkeypatch, [_bundle("dissented", upstream_yanked=True)])

        assert _key("dissented") in seen["upstream_yanked"]
        assert _key("dissented") not in seen["upstream_present"]

    def test_plain_yank_still_classifies_as_yanked(self, monkeypatch):
        seen = self._capture(monkeypatch, [_bundle("retired", yanked=True)])

        assert _key("retired") in seen["upstream_yanked"]
        assert _key("retired") not in seen["upstream_present"]

    def test_clean_bundle_is_still_present(self, monkeypatch):
        # The fix must not over-reach: an ordinary live bundle stays present.
        seen = self._capture(monkeypatch, [_bundle("healthy")])

        assert _key("healthy") in seen["upstream_present"]
        assert _key("healthy") not in seen["upstream_yanked"]

    def test_both_flags_set_classifies_as_yanked(self, monkeypatch):
        seen = self._capture(monkeypatch, [_bundle("both", yanked=True, upstream_yanked=True)])

        assert _key("both") in seen["upstream_yanked"]
        assert _key("both") not in seen["upstream_present"]


class TestTombstoneNotDuplicated:
    """A mirrored nuke leaves the row, so the branch re-fires on every sync."""

    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'tombstone.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield
        asyncio.run(dispose_engine())

    def test_repeated_sync_records_one_tombstone(self):
        from sqlalchemy import select

        from cvcpkg.server.db import PackageTombstoneRow, get_session
        from cvcpkg.server.db_stores import DbPackageIndex

        store = DbPackageIndex()

        async def scenario():
            await store.add_package(
                name="gone",
                version="1.0.0+cvc.1",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="0" * 64,
                size_bytes=1,
                archive_url="/v1/download/gone.tar.zst",
                origin_upstream=UPSTREAM,
            )
            counts = []
            # Upstream nuked it: absent from the catalogue, present in tombstones.
            for _ in range(3):
                counts.append(
                    await store.reconcile_from_upstream(
                        UPSTREAM,
                        upstream_yanked=set(),
                        upstream_present=set(),
                        upstream_tombstoned={_key("gone")},
                    )
                )
            async with get_session() as session:
                rows = (await session.execute(select(PackageTombstoneRow))).scalars().all()
            return counts, rows

        counts, rows = asyncio.run(scenario())

        assert len(rows) == 1, f"one upstream nuke must leave one tombstone, got {len(rows)}"
        assert counts[0]["tombstoned"] == 1
        # Later syncs must not keep re-counting it; that number is surfaced to
        # operators as an eviction total and would grow without bound.
        assert counts[1]["tombstoned"] == 0
        assert counts[2]["tombstoned"] == 0


class TestMirroredNukeStopsServingBytes:
    """A nuke inherited from upstream must also stop downloads.

    A local nuke deletes the archive, so absence alone produced the 410.  A
    mirrored nuke deliberately keeps the bytes for the ordinary yank-retention
    GC -- and the tombstone lookup was nested inside "if the file is missing",
    so the mirror happily kept serving a bundle its upstream had nuked. Found
    live: prod answered 410 for zzz-authprobe while dev, which had correctly
    recorded the tombstone, answered 200 for the same archive.
    """

    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'gone.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield
        asyncio.run(dispose_engine())

    ARCHIVE = "gone-1.0.0+cvc.1-linux-x86_64-release-shared.tar.zst"

    def _seed(self, *, yanked_after_reconcile=True):
        from cvcpkg.server.db_stores import DbPackageIndex

        store = DbPackageIndex()

        async def scenario():
            await store.add_package(
                name="gone",
                version="1.0.0+cvc.1",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="0" * 64,
                size_bytes=1,
                archive_url=f"/v1/download/{self.ARCHIVE}",
                origin_upstream=UPSTREAM,
            )
            if yanked_after_reconcile:
                await store.reconcile_from_upstream(
                    UPSTREAM,
                    upstream_yanked=set(),
                    upstream_present=set(),
                    upstream_tombstoned={_key("gone")},
                )
            return (
                await store.get_tombstone_by_filename(self.ARCHIVE),
                await store.get_archive_is_yanked(self.ARCHIVE),
            )

        return asyncio.run(scenario())

    def test_upstream_nuke_leaves_a_tombstone_and_a_yanked_row(self):
        ts, yanked = self._seed()

        assert ts is not None, "the mirrored nuke must be recorded"
        assert ts["reason"] == "upstream"
        # The row survives on purpose -- retention GC owns the bytes -- which is
        # exactly why absence cannot be the 410 trigger.
        assert yanked is True

    def test_a_live_republished_variant_is_not_reported_as_yanked(self):
        # No reconcile: the row is present and un-yanked. A stale tombstone for
        # an older incarnation must not make the new one unreachable.
        _ts, yanked = self._seed(yanked_after_reconcile=False)

        assert yanked is False

    def test_unknown_archive_reports_none(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        store = DbPackageIndex()
        assert asyncio.run(store.get_archive_is_yanked("no-such-archive.tar.zst")) is None


class TestOriginUpstreamBackfill:
    """Migration 022: reconciliation must reach rows imported before the column."""

    def test_backfill_stamps_populate_rows_from_recorded_provenance(self, tmp_path):
        pytest.importorskip("alembic", reason="alembic required for migration tests")
        import sqlalchemy as sa

        db = tmp_path / "backfill.db"
        engine = sa.create_engine(f"sqlite:///{db}")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE packages ("
                    " id INTEGER PRIMARY KEY, origin_upstream TEXT,"
                    " published_by TEXT, org_slug TEXT)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO packages (origin_upstream, published_by, org_slug) VALUES"
                    " ('', 'populate:https://cvcpkg.org', ''),"
                    " ('', 'populate:https://pkg.example.org', ''),"
                    " ('', 'builder-01', ''),"
                    " ('', 'populate:https://cvcpkg.org', 'acme'),"
                    " ('https://already.example', 'populate:https://cvcpkg.org', '')"
                )
            )

        # Drive the migration body directly against this connection.
        import importlib.util

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        spec = importlib.util.spec_from_file_location(
            "m022",
            "src/cvcpkg/migrations/versions/2026_07_18_022_backfill_origin_upstream.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()

        with engine.connect() as conn:
            got = conn.execute(
                sa.text("SELECT published_by, org_slug, origin_upstream FROM packages ORDER BY id")
            ).all()

        by_upstream = [r[2] for r in got]
        # populate rows get the upstream recorded in their own published_by --
        # not the currently configured one, so two different upstreams survive.
        assert by_upstream[0] == "https://cvcpkg.org"
        assert by_upstream[1] == "https://pkg.example.org"
        # A locally published package is never claimed by any upstream; that was
        # migration 020's real safety concern and it still holds.
        assert by_upstream[2] == ""
        # org-scoped rows are out of populate's scope entirely.
        assert by_upstream[3] == ""
        # An existing stamp is never overwritten.
        assert by_upstream[4] == "https://already.example"


class TestTrustMirrorFlag:
    """The flag must be able to say no, not just yes."""

    def test_no_trust_mirror_overrides_inherited_env(self, monkeypatch):
        from cvcpkg.catalog import trust_mirror_default

        monkeypatch.setenv("CVCPKG_TRUST_MIRROR", "1")
        assert trust_mirror_default() is True

        # What --no-trust-mirror writes must actually read back as False,
        # otherwise the negation is decorative.
        monkeypatch.setenv("CVCPKG_TRUST_MIRROR", "0")
        assert trust_mirror_default() is False

    def test_default_is_upstream_authority(self, monkeypatch):
        from cvcpkg.catalog import trust_mirror_default

        monkeypatch.delenv("CVCPKG_TRUST_MIRROR", raising=False)
        assert trust_mirror_default() is False

    @pytest.mark.parametrize("cmd", ["install", "search"])
    def test_both_commands_expose_the_negation(self, cmd):
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        result = CliRunner().invoke(cli, [cmd, "--help"])
        assert result.exit_code == 0
        assert "--no-trust-mirror" in result.output


def _catalog_file(tmp_path, *, upstream_yanked):
    """A one-bundle local catalog, so install resolves without a network."""
    import yaml

    b = _bundle("chainpkg", upstream_yanked=upstream_yanked)
    b["sha256"] = "0" * 64
    b["size_bytes"] = 1
    b["archive_url"] = "http://127.0.0.1:1/chainpkg.tar.gz"
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"bundles": [b]}), encoding="utf-8")
    return p


class TestTrustMirrorIsNotProcessGlobal:
    """--trust-mirror is one command's decision, not the process's.

    It used to be passed down by writing ``os.environ["CVCPKG_TRUST_MIRROR"]``
    and never restoring it.  In any long-lived or embedding process -- an IDE
    plugin, a server, a test session -- a single ``install --trust-mirror``
    then silently changed resolution policy for everything that ran after it,
    reinstating bundles an upstream had withdrawn for a CVE.  It also made the
    in-process CLI tests order-dependent: the leak from a ``--trust-mirror``
    run is what broke test_authority_chain's upstream-wins assertion whenever
    the full suite ran, while it passed on its own.
    """

    def _invoke_and_read_env(self, argv, monkeypatch):
        """Run *argv*, then report what it left in the environment.

        Popped before asserting so a regression cannot poison the rest of the
        session -- the failure mode under test is precisely a leak that outlives
        the command.
        """
        import os

        from click.testing import CliRunner

        from cvcpkg.cli import cli

        monkeypatch.delenv("CVCPKG_TRUST_MIRROR", raising=False)
        # install would otherwise try to publish telemetry / fetch mirror URLs.
        monkeypatch.delenv("CVCPKG_SERVER_URL", raising=False)
        try:
            result = CliRunner().invoke(cli, argv)
        finally:
            leaked = os.environ.pop("CVCPKG_TRUST_MIRROR", None)
        return result, leaked

    @pytest.mark.parametrize("flag", ["--trust-mirror", "--no-trust-mirror"])
    def test_install_does_not_export_the_flag(self, flag, tmp_path, monkeypatch):
        import cvcpkg.installer as installer

        monkeypatch.setattr(installer, "install_entry", lambda *a, **kw: None)
        cat = _catalog_file(tmp_path, upstream_yanked=False)
        result, leaked = self._invoke_and_read_env(
            [
                "install",
                "chainpkg",
                "--catalog",
                str(cat),
                "--prefix",
                str(tmp_path / "px"),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                flag,
            ],
            monkeypatch,
        )
        # Not incidental: had it bailed before resolution, "nothing leaked"
        # would be true for the wrong reason.
        assert result.exit_code == 0, result.output
        assert leaked is None, (
            f"install {flag} left CVCPKG_TRUST_MIRROR={leaked!r} in os.environ; "
            "every later resolution in this process now inherits that policy"
        )

    @pytest.mark.parametrize("flag", ["--trust-mirror", "--no-trust-mirror"])
    def test_search_does_not_export_the_flag(self, flag, tmp_path, monkeypatch):
        import httpx

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"total": 0, "package_count": 0, "packages": []}

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Client)
        result, leaked = self._invoke_and_read_env(["search", "zlib", flag], monkeypatch)
        assert result.exit_code == 0, result.output
        assert leaked is None, (
            f"search {flag} left CVCPKG_TRUST_MIRROR={leaked!r} in os.environ; "
            "every later resolution in this process now inherits that policy"
        )

    def test_install_still_honours_the_flag_without_the_env(self, tmp_path, monkeypatch):
        """Threading it through must not make the flag decorative.

        Same catalog both times, and the only bundle in it is one the upstream
        retired: default resolution must refuse it, --trust-mirror must select
        it.  Without this, "no leak" would also be satisfied by a flag that
        stopped doing anything at all.
        """
        import cvcpkg.installer as installer

        installed = []
        monkeypatch.setattr(
            installer, "install_entry", lambda e, *a, **kw: installed.append(e.name)
        )
        cat = _catalog_file(tmp_path, upstream_yanked=True)
        argv = [
            "install",
            "chainpkg",
            "--catalog",
            str(cat),
            "--prefix",
            str(tmp_path / "px"),
            "--platform",
            "linux",
            "--arch",
            "x86_64",
        ]

        refused, _ = self._invoke_and_read_env(argv, monkeypatch)
        assert refused.exit_code != 0, refused.output
        assert "no bundles found" in refused.output
        assert installed == []

        opted_in, _ = self._invoke_and_read_env(argv + ["--trust-mirror"], monkeypatch)
        assert opted_in.exit_code == 0, opted_in.output
        assert installed == ["chainpkg"]


class TestSearchHonoursUpstreamAuthority:
    """search bypasses catalog_entries(), so it needs the policy applied too."""

    def _run(self, monkeypatch, argv, env=None):
        import httpx
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        payload = {
            "total": 2,
            "package_count": 2,
            "packages": [
                {
                    "name": "clean",
                    "version": "1.0.0+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "link": "shared",
                    "build_type": "release",
                    "size_bytes": 10,
                    "yanked": False,
                    "upstream_yanked": False,
                },
                {
                    "name": "retired-upstream",
                    "version": "1.0.0+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "link": "shared",
                    "build_type": "release",
                    "size_bytes": 10,
                    # This mirror still serves it; upstream retired it.
                    "yanked": False,
                    "upstream_yanked": True,
                },
            ],
        }

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return payload

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Client)
        monkeypatch.delenv("CVCPKG_TRUST_MIRROR", raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        return CliRunner().invoke(cli, ["search", *argv])

    def test_default_hides_a_bundle_upstream_retired(self, monkeypatch):
        result = self._run(monkeypatch, ["zlib"])
        assert result.exit_code == 0, result.output
        assert "clean" in result.output
        assert "retired-upstream" not in result.output

    def test_trust_mirror_shows_it(self, monkeypatch):
        result = self._run(monkeypatch, ["zlib", "--trust-mirror"])
        assert result.exit_code == 0, result.output
        assert "retired-upstream" in result.output

    def test_no_trust_mirror_beats_the_environment(self, monkeypatch):
        # Without a working negation an exported CVCPKG_TRUST_MIRROR=1 would be
        # impossible to override from the command line.
        result = self._run(
            monkeypatch,
            ["zlib", "--no-trust-mirror"],
            env={"CVCPKG_TRUST_MIRROR": "1"},
        )
        assert result.exit_code == 0, result.output
        assert "retired-upstream" not in result.output


class TestChunkedFinalise409:
    """A 409 at finalise means the bytes landed, not that the build failed."""

    def _fake_httpx(self, monkeypatch, *, complete_code, complete_payload, file_size):
        import httpx

        class _Resp:
            def __init__(self, code, payload=None):
                self.status_code = code
                self._payload = payload if payload is not None else {}
                self.text = str(self._payload)

            def json(self):
                return self._payload

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, **kw):
                if url.endswith("/v1/upload/init"):
                    return _Resp(201, {"upload_id": "u1", "chunk_size": 1 << 20})
                if url.endswith("/complete"):
                    return _Resp(complete_code, complete_payload)
                return _Resp(500, {})

            def patch(self, url, **kw):
                # The loop advances `offset` to whatever the server says it
                # holds, so report the whole file as received to finish in one
                # pass rather than spinning.
                return _Resp(200, {"bytes_received": file_size})

            def get(self, url, **kw):
                return _Resp(200, {"bytes_received": file_size})

        monkeypatch.setattr(httpx, "Client", _Client)

    def test_finalise_conflict_reports_skipped(self, monkeypatch, tmp_path):
        from cvcpkg.cli import _publish

        archive = tmp_path / "pkg.tar.zst"
        archive.write_bytes(b"x" * 32)

        # The race window spans the whole multi-minute upload, so a concurrent
        # publish of the same variant surfaces here rather than at init.
        self._fake_httpx(
            monkeypatch,
            complete_code=409,
            complete_payload={"detail": "variant already published"},
            file_size=archive.stat().st_size,
        )

        result = _publish._publish_chunked(
            base="https://example.invalid",
            headers={},
            params={},
            archive_path=archive,
            file_size=archive.stat().st_size,
        )
        assert result == "skipped"

    def test_finalise_success_still_reports_published(self, monkeypatch, tmp_path):
        from cvcpkg.cli import _publish

        archive = tmp_path / "pkg.tar.zst"
        archive.write_bytes(b"x" * 32)
        self._fake_httpx(
            monkeypatch,
            complete_code=200,
            complete_payload={"sha256": "a" * 64},
            file_size=archive.stat().st_size,
        )

        result = _publish._publish_chunked(
            base="https://example.invalid",
            headers={},
            params={},
            archive_path=archive,
            file_size=archive.stat().st_size,
        )
        assert result == "published"

    def test_finalise_real_error_still_raises(self, monkeypatch, tmp_path):
        import click

        from cvcpkg.cli import _publish

        archive = tmp_path / "pkg.tar.zst"
        archive.write_bytes(b"x" * 32)
        self._fake_httpx(
            monkeypatch,
            complete_code=500,
            complete_payload={"detail": "boom"},
            file_size=archive.stat().st_size,
        )

        # The whole point of the re-raise is that genuine failures still fail.
        with pytest.raises(click.ClickException):
            _publish._publish_chunked(
                base="https://example.invalid",
                headers={},
                params={},
                archive_path=archive,
                file_size=archive.stat().st_size,
            )
