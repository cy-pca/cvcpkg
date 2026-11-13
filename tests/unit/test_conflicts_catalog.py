"""A recipe's conflicts: must reach the catalog, and `cvcpkg install` must use it.

``mujoco`` (MuJoCo 4) and ``mujoco3`` (MuJoCo 3.17) install the same files and
declare ``conflicts:`` on each other.  ``cvcpkg install`` read those
declarations only from local recipe dirs (normally the recipes bundled in the
client), so a package newer than the client's recipes got no check: on
2026-11-13, with client 2.5.6, ``cvcpkg install mujoco`` into a prefix holding
``mujoco3`` silently overwrote it.

Covered here:

* server, on the database and the JSON-index backends: every publish path
  (query parameter, multipart field, chunked-upload query or JSON body)
  records ``conflicts``; ``/v1/catalog`` and ``/v1/packages/{name}`` serve it;
  malformed lists are refused; ``backfill-deps`` fills it only while empty;
* client: ``_check_conflicts`` refuses a conflicting pair from catalog data
  alone (``conflicts`` and ``provides`` slots), in both directions and for a
  one-sided declaration, using the installed manifest or the catalog for what
  is already in the prefix, and still passes unrelated packages.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path

import click
import pytest

from cvcpkg.catalog import catalog_entries, exclusive_with

VARIANT = {"platform": "linux", "arch": "x86_64", "build_type": "release", "link": "shared"}


# ── exclusive_with ──────────────────────────────────────────────


class TestExclusiveWith:
    def test_a_declared_pair_excludes_both_ways(self):
        out = exclusive_with({"mujoco": ([], []), "mujoco3": (["mujoco"], [])})
        assert out == {"mujoco": {"mujoco3"}, "mujoco3": {"mujoco"}}

    def test_slot_providers_exclude_each_other(self):
        out = exclusive_with(
            {"pytest-cp311": ([], ["pytest"]), "pytest-cp312": ([], ["pytest"]), "zlib": ([], [])}
        )
        assert out == {"pytest-cp311": {"pytest-cp312"}, "pytest-cp312": {"pytest-cp311"}}

    def test_names_outside_the_set_and_self_are_ignored(self):
        assert exclusive_with({"a": (["a", "elsewhere"], ["slot"])}) == {}


# ── server: publish -> store -> catalog ─────────────────────────


@pytest.fixture(params=["db", "index"])
def server(request, tmp_path, monkeypatch):
    """A TestClient and admin auth header, on either catalog backend."""
    pytest.importorskip("fastapi", reason="server extras not installed")
    pytest.importorskip("aiosqlite", reason="aiosqlite required")
    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.models import TokenRole

    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)
    if request.param == "db":
        from cvcpkg.server.db import create_tables, dispose_engine, init_db
        from cvcpkg.server.db_stores import DbTokenStore

        db_url = f"sqlite+aiosqlite:///{tmp_path / 'conflicts.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        async def _seed():
            init_db(db_url)
            await create_tables()
            token = await DbTokenStore(tmp_path).create("test-admin", TokenRole.admin)
            await dispose_engine()
            return token

        token = asyncio.run(_seed())
    else:
        from cvcpkg.server.auth import TokenStore

        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        token = TokenStore(tmp_path).create("test-admin", TokenRole.admin)
    with TestClient(create_app(state_dir=tmp_path)) as client:
        yield client, {"Authorization": f"Bearer {token}"}


def _publish(client, hdr, name, version, *, data=None, **query):
    return client.post(
        "/v1/publish",
        params={"name": name, "version": version, **VARIANT, **query},
        data=data,
        files={"file": (f"{name}.tar.zst", io.BytesIO(f"{name}=={version}".encode()))},
        headers=hdr,
    )


def _catalog(client) -> dict:
    return client.get("/v1/catalog").json()


def _bundle(client, name) -> dict:
    (b,) = (b for b in _catalog(client)["bundles"] if b["name"] == name)
    return b


class TestRoundTrip:
    def test_query_parameter_reaches_the_catalog(self, server):
        client, hdr = server
        resp = _publish(client, hdr, "mujoco3", "3.17.0+cvc.1", conflicts=json.dumps(["mujoco"]))
        assert resp.status_code == 200, resp.text
        assert _bundle(client, "mujoco3")["conflicts"] == ["mujoco"]

    def test_package_listing_serves_it_too(self, server):
        client, hdr = server
        _publish(client, hdr, "mujoco3", "3.17.0+cvc.1", conflicts=json.dumps(["mujoco"]))
        resp = client.get(
            "/v1/packages/mujoco3/latest", params={"platform": "linux", "arch": "x86_64"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["conflicts"] == ["mujoco"]

    def test_a_publish_without_it_records_none(self, server):
        client, hdr = server
        assert _publish(client, hdr, "zlib", "1.3.1+cvc.1").status_code == 200
        assert _bundle(client, "zlib")["conflicts"] == []

    def test_the_multipart_field_wins_over_the_query(self, server):
        client, hdr = server
        resp = _publish(
            client,
            hdr,
            "mujoco3",
            "3.17.0+cvc.1",
            data={"conflicts": json.dumps(["mujoco"])},
            conflicts=json.dumps(["from-query"]),
        )
        assert resp.status_code == 200, resp.text
        assert _bundle(client, "mujoco3")["conflicts"] == ["mujoco"]

    def test_chunked_upload_takes_it_in_the_query_or_the_body(self, server):
        client, hdr = server
        for name, query, body in (
            ("by-query", {"conflicts": json.dumps(["other"])}, None),
            ("by-body", {}, {"conflicts": ["other"]}),
        ):
            content = name.encode()
            resp = client.post(
                "/v1/upload/init",
                params={
                    "name": name,
                    "version": "1.0+cvc.1",
                    **VARIANT,
                    "total_size": len(content),
                    **query,
                },
                json=body,
                headers=hdr,
            )
            assert resp.status_code == 201, resp.text
            upload_id = resp.json()["upload_id"]
            resp = client.patch(
                f"/v1/upload/{upload_id}",
                content=content,
                headers={**hdr, "Content-Type": "application/octet-stream"},
            )
            assert resp.status_code == 200, resp.text
            resp = client.post(
                f"/v1/upload/{upload_id}/complete",
                params={"expected_sha256": hashlib.sha256(content).hexdigest()},
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text
            assert _bundle(client, name)["conflicts"] == ["other"], name

    @pytest.mark.parametrize("bad", ["mujoco", '{"mujoco": true}', '["mujoco", ""]', "[1]"])
    def test_a_malformed_list_is_a_422(self, server, bad):
        client, hdr = server
        resp = _publish(client, hdr, "bad-conflicts", "1.0+cvc.1", conflicts=bad)
        assert resp.status_code == 422, resp.text
        assert "conflicts" in resp.json()["detail"]
        assert not [b for b in _catalog(client)["bundles"] if b["name"] == "bad-conflicts"]


class TestBackfill:
    PATH = "/v1/admin/packages/mujoco/4.0.0%2Bcvc.2/backfill-deps"

    def test_fills_an_empty_row_once(self, server):
        client, hdr = server
        assert _publish(client, hdr, "mujoco", "4.0.0+cvc.2").status_code == 200
        resp = client.post(self.PATH, params=VARIANT, json={"conflicts": ["mujoco3"]}, headers=hdr)
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"]["conflicts"] == 1
        assert _bundle(client, "mujoco")["conflicts"] == ["mujoco3"]
        # Fill-only: a row that records a list is never rewritten.
        resp = client.post(self.PATH, params=VARIANT, json={"conflicts": ["other"]}, headers=hdr)
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"]["conflicts"] == 0
        assert _bundle(client, "mujoco")["conflicts"] == ["mujoco3"]

    def test_an_empty_name_is_a_422(self, server):
        client, hdr = server
        assert _publish(client, hdr, "mujoco", "4.0.0+cvc.2").status_code == 200
        resp = client.post(self.PATH, params=VARIANT, json={"conflicts": [""]}, headers=hdr)
        assert resp.status_code == 422, resp.text


# ── client: _check_conflicts from catalog data ──────────────────


def _bundle_dict(name, version="1.0+cvc.1", **extra) -> dict:
    return {
        "name": name,
        "version": version,
        **VARIANT,
        "sha256": "0" * 64,
        "size_bytes": 1,
        "archive_url": f"https://example.invalid/{name}.tar.zst",
        **extra,
    }


def _entries(*bundles: dict):
    return catalog_entries({"bundles": list(bundles)}, platform="linux", arch="x86_64")


def _install_into(prefix: Path, name: str, version: str, manifest: dict | None = None) -> None:
    """Record *name* as installed: a lockfile row and, optionally, its manifest."""
    import yaml

    from cvcpkg.lockfile import LockEntry, Lockfile

    lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
    bundles = Lockfile.read(lock_path).bundles if lock_path.is_file() else []
    Lockfile(
        platform="linux",
        arch="x86_64",
        config="release",
        link="shared",
        bundles=[*bundles, LockEntry(name=name, version=version)],
    ).write(lock_path)
    if manifest is not None:
        mdir = prefix / "share" / "libcvc-deps" / name
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


def _check(installing, prefix, entries, recipe_dirs=None):
    from cvcpkg.cli._install import _check_conflicts

    by_name = {e.name: e for e in entries}
    picked = {n: by_name[n] for n in installing if n in by_name}
    _check_conflicts(list(installing), prefix, recipe_dirs, picked=picked, entries=entries)


MUJOCO = _bundle_dict("mujoco", "4.0.0+cvc.2", conflicts=["mujoco3"])
MUJOCO3 = _bundle_dict("mujoco3", "3.17.0+cvc.1", conflicts=["mujoco"])


class TestCheckConflictsFromCatalog:
    def test_installing_onto_a_conflicting_package_is_refused(self, tmp_path):
        # The 2026-11-13 case: mujoco3 installed, then `cvcpkg install mujoco`,
        # with no recipe dir that knows either package.
        entries = _entries(MUJOCO, MUJOCO3)
        _install_into(tmp_path, "mujoco3", "3.17.0+cvc.1")
        with pytest.raises(click.ClickException) as exc:
            _check(["mujoco"], tmp_path, entries)
        msg = exc.value.format_message()
        assert "'mujoco' conflicts with installed package 'mujoco3'" in msg
        assert "cvcpkg uninstall mujoco3" in msg

    def test_co_requesting_a_conflicting_pair_is_refused(self, tmp_path):
        entries = _entries(MUJOCO, MUJOCO3)
        with pytest.raises(click.ClickException, match="co-requested"):
            _check(["mujoco", "mujoco3"], tmp_path, entries)

    def test_a_one_sided_declaration_fires_in_both_directions(self, tmp_path):
        # Only mujoco3 declares the pair (as on rows not yet backfilled).
        entries = _entries(_bundle_dict("mujoco", "4.0.0+cvc.2"), MUJOCO3)
        _install_into(tmp_path, "mujoco3", "3.17.0+cvc.1")
        with pytest.raises(click.ClickException, match="installed package 'mujoco3'"):
            _check(["mujoco"], tmp_path, entries)
        other = tmp_path / "other"
        _install_into(other, "mujoco", "4.0.0+cvc.2")
        with pytest.raises(click.ClickException, match="installed package 'mujoco'"):
            _check(["mujoco3"], other, entries)

    def test_providers_of_a_slot_are_refused_together(self, tmp_path):
        entries = _entries(
            _bundle_dict("pytest-cp311", provides=["pytest"]),
            _bundle_dict("pytest-cp312", provides=["pytest"]),
        )
        _install_into(tmp_path, "pytest-cp311", "1.0+cvc.1")
        with pytest.raises(click.ClickException, match="installed package 'pytest-cp311'"):
            _check(["pytest-cp312"], tmp_path, entries)

    def test_the_installed_manifest_counts_when_the_catalog_lacks_the_package(self, tmp_path):
        # A package built from source is in the prefix but not in the catalog;
        # its manifest names the conflict.
        entries = _entries(_bundle_dict("mujoco", "4.0.0+cvc.2"))
        _install_into(
            tmp_path,
            "mujoco3",
            "3.17.0+cvc.1",
            manifest={"bundle": {"name": "mujoco3"}, "conflicts": ["mujoco"]},
        )
        with pytest.raises(click.ClickException, match="installed package 'mujoco3'"):
            _check(["mujoco"], tmp_path, entries)

    def test_unrelated_and_reinstalled_packages_pass(self, tmp_path):
        entries = _entries(MUJOCO, MUJOCO3, _bundle_dict("zlib"))
        _install_into(tmp_path, "mujoco3", "3.17.0+cvc.1")
        _check(["zlib"], tmp_path, entries)
        _check(["mujoco3"], tmp_path, entries)

    def test_stale_bundled_recipes_do_not_hide_catalog_conflicts(self, tmp_path):
        # A recipe dir that has neither package, like a client older than them.
        rd = tmp_path / "recipes"
        rd.mkdir()
        entries = _entries(MUJOCO, MUJOCO3)
        _install_into(tmp_path, "mujoco3", "3.17.0+cvc.1")
        with pytest.raises(click.ClickException, match="installed package 'mujoco3'"):
            _check(["mujoco"], tmp_path, entries, recipe_dirs=[rd])

    def test_a_catalog_without_the_field_changes_nothing(self, tmp_path):
        # A catalog from a server older than migration 038.
        entries = _entries(_bundle_dict("mujoco", "4.0.0+cvc.2"), _bundle_dict("mujoco3"))
        _install_into(tmp_path, "mujoco3", "3.17.0+cvc.1")
        _check(["mujoco"], tmp_path, entries)


# ── bundle manifest and catalog entry ───────────────────────────


class TestManifestAndCatalogEntry:
    def test_catalog_entry_reads_conflicts(self):
        (e,) = _entries(MUJOCO3)
        assert e.conflicts == ["mujoco"]

    def test_catalog_entry_defaults_to_none(self):
        (e,) = _entries(_bundle_dict("zlib"))
        assert e.conflicts == []

    def test_bundle_manifest_reads_the_top_level_key(self):
        from cvcpkg.manifest import BundleManifest

        m = BundleManifest.from_dict(
            {
                "schema_version": 3,
                "bundle": {
                    "name": "mujoco3",
                    "version": "3.17.0+cvc.1",
                    "upstream_version": "3.17.0",
                    "cvc_revision": 1,
                    **VARIANT,
                },
                "conflicts": ["mujoco"],
            }
        )
        assert m.conflicts == ["mujoco"]
