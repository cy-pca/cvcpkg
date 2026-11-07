"""A publish never stores its archive over the file another row stores.

The duplicate check is per coordinate, but the archive's name is not unique to
one: migration 036 relabelled rows ``none/none`` and kept their
``...-release-shared.tar.zst`` names, and an org's twin of a public package is
named alike.  On 2026-10-31 dev-builder-01b re-published six relabelled
traitlets/traittypes windows rows as ``release/shared``, and ``/v1/publish``
wrote over the relabelled rows' files, so they served bytes failing their
sha256.  Both publish paths (``/v1/publish`` and ``/v1/upload/{id}/complete``)
are covered.
"""

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")

from fastapi.testclient import TestClient

from cvcpkg.server import app as app_mod
from cvcpkg.server.app import create_app
from cvcpkg.server.models import TokenRole

_VIAS = ("publish", "chunked")

# One of the six dev rows: published release/shared, relabelled none/none by 036.
_TRAITLETS = dict(name="traitlets-cp313", version="5.14.3+cvc.1", platform="windows", arch="x86_64")
_SHARED = dict(build_type="release", link="shared")
_NEUTRAL = dict(build_type="none", link="none")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        raw = await DbTokenStore(tmp_path).create("builder", TokenRole.publisher)
        await dispose_engine()
        return raw

    token = asyncio.run(_seed())
    with TestClient(create_app(state_dir=tmp_path)) as client:
        archives = app_mod._get_state().archives_dir()
        archives.mkdir(parents=True, exist_ok=True)
        yield client, {"Authorization": f"Bearer {token}"}, archives


def _add_row(client, archives, data: bytes, archive_name: str, **coords) -> None:
    """A row already in the catalog, and the file it stores."""
    (archives / archive_name).write_bytes(data)

    async def _add():
        await app_mod._db_packages.add_package(
            sha256=_sha(data),
            size_bytes=len(data),
            archive_url=f"/v1/download/{archive_name}",
            published_by="earlier",
            **coords,
        )

    client.portal.call(_add)


def _rows(client):
    async def _get():
        pkgs, _ = await app_mod._db_packages.get_bundles(include_yanked=True)
        return pkgs

    return client.portal.call(_get)


def _publish(client, hdrs, via: str, data: bytes, **params):
    if via == "publish":
        return client.post(
            "/v1/publish",
            params=params,
            files={"file": ("a.tar.zst", io.BytesIO(data))},
            headers=hdrs,
        )
    resp = client.post("/v1/upload/init", params=params, headers=hdrs)
    assert resp.status_code == 201, resp.text
    upload_id = resp.json()["upload_id"]
    resp = client.patch(
        f"/v1/upload/{upload_id}",
        content=data,
        headers={**hdrs, "Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200, resp.text
    return client.post(
        f"/v1/upload/{upload_id}/complete",
        params={"expected_sha256": _sha(data)},
        headers=hdrs,
    )


def _assert_each_row_serves_its_bytes(rows, archives) -> None:
    """What `cvcpkg install` checks: every row's file hashes to its sha256."""
    for p in rows:
        stored = archives / p.archive_url.rsplit("/", 1)[-1]
        assert _sha(stored.read_bytes()) == p.sha256, p.archive_url


def _no_dup(monkeypatch) -> None:
    """Both publishes passed the duplicate pre-check before either row
    existed.  It ignores the org, so only a race gets an org's twin past it."""

    async def _false(*a, **k):
        return False

    monkeypatch.setattr(app_mod._db_packages, "check_duplicate", _false)


@pytest.mark.parametrize("via", _VIAS)
def test_relabelled_row_keeps_its_archive(env, via):
    client, hdrs, archives = env
    relabelled, new = b"the relabelled row's build", b"dev-builder-01b's re-publish"
    taken = app_mod._safe_archive_filename(**_TRAITLETS, **_SHARED)
    _add_row(client, archives, relabelled, taken, **_TRAITLETS, **_NEUTRAL)

    resp = _publish(client, hdrs, via, new, **_TRAITLETS, **_SHARED)

    assert resp.status_code == 200, resp.text
    assert (archives / taken).read_bytes() == relabelled
    rows = _rows(client)
    assert len(rows) == 2
    _assert_each_row_serves_its_bytes(rows, archives)
    qualified = app_mod._sha_qualified_archive_filename(taken, _sha(new))
    assert resp.json()["archive_url"] == f"/v1/download/{qualified}"


@pytest.mark.parametrize("via", _VIAS)
def test_org_twin_keeps_its_archive(env, monkeypatch, via):
    client, hdrs, archives = env
    org_bytes, public = b"acme's build", b"the public build"
    taken = app_mod._safe_archive_filename(**_TRAITLETS, **_SHARED)
    _add_row(client, archives, org_bytes, taken, **_TRAITLETS, **_SHARED, org_slug="acme")
    _no_dup(monkeypatch)

    resp = _publish(client, hdrs, via, public, **_TRAITLETS, **_SHARED)

    assert resp.status_code == 200, resp.text
    assert (archives / taken).read_bytes() == org_bytes
    rows = _rows(client)
    assert sorted(p.org for p in rows) == ["", "acme"]
    _assert_each_row_serves_its_bytes(rows, archives)


@pytest.mark.parametrize("via", _VIAS)
def test_free_name_is_the_conventional_one(env, via):
    client, hdrs, archives = env
    resp = _publish(client, hdrs, via, b"data", **_TRAITLETS, **_SHARED)

    assert resp.status_code == 200, resp.text
    name = app_mod._safe_archive_filename(**_TRAITLETS, **_SHARED)
    assert resp.json()["archive_url"] == f"/v1/download/{name}"
    _assert_each_row_serves_its_bytes(_rows(client), archives)


@pytest.mark.parametrize("via", _VIAS)
def test_both_names_taken_is_refused(env, via):
    client, hdrs, archives = env
    new = b"a third build"
    taken = app_mod._safe_archive_filename(**_TRAITLETS, **_SHARED)
    qualified = app_mod._sha_qualified_archive_filename(taken, _sha(new))
    _add_row(client, archives, b"relabelled", taken, **_TRAITLETS, **_NEUTRAL)
    _add_row(
        client, archives, b"elsewhere", qualified, **{**_TRAITLETS, "arch": "arm64"}, **_SHARED
    )
    before = sorted(p.name for p in archives.iterdir())

    resp = _publish(client, hdrs, via, new, **_TRAITLETS, **_SHARED)

    assert resp.status_code == 409, resp.text
    rows = _rows(client)
    assert len(rows) == 2
    assert sorted(p.name for p in archives.iterdir()) == before  # no temp file left
    _assert_each_row_serves_its_bytes(rows, archives)
