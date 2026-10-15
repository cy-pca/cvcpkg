"""Publishes whose metadata cannot fit a ``packages`` column are refused with a
422 up front, and ``packages.license`` holds real multi-licence SPDX
expressions (it was VARCHAR(128); litecoin-core's licence is 129 characters,
and PostgreSQL turned that publish into a bare 500 after the upload)."""

from __future__ import annotations

import asyncio
import importlib.util
import io
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
COIN_LICENSE = (
    "MIT AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND Sleepycat AND OpenSSL "
    "AND LGPL-3.0-or-later WITH LGPL-3.0-linking-exception"
)


def test_license_column_is_wide_enough():
    pytest.importorskip("sqlalchemy")
    from cvcpkg.server.db import PackageRow

    assert len(COIN_LICENSE) == 129
    assert PackageRow.__table__.c.license.type.length == 1024


def test_migration_030_widens_to_the_orm_length():
    pytest.importorskip("alembic")
    from cvcpkg.server.db import PackageRow

    path = _ROOT / "src/cvcpkg/migrations/versions/2026_10_13_030_widen_packages_license.py"
    spec = importlib.util.spec_from_file_location("m030", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "029"
    calls = []
    mod._alter = lambda a, b: calls.append((a, b))
    mod.upgrade()
    assert calls == [(128, PackageRow.__table__.c.license.type.length)]


@pytest.fixture(scope="module")
def server_env(tmp_path_factory):
    pytest.importorskip("fastapi", reason="server extras not installed")
    import os

    from fastapi.testclient import TestClient

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore
    from cvcpkg.server.models import TokenRole

    tmp_path = tmp_path_factory.mktemp("field_lengths")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'f.db'}"
    prev_db = os.environ.get("CVCPKG_DATABASE_URL")
    prev_mirror = os.environ.get("CVCPKG_MIRROR_MODE")
    os.environ["CVCPKG_DATABASE_URL"] = db_url
    os.environ.pop("CVCPKG_MIRROR_MODE", None)

    async def _seed():
        init_db(db_url)
        await create_tables()
        admin = await DbTokenStore(tmp_path).create("test-admin", TokenRole.admin)
        await dispose_engine()
        return admin

    admin_token = asyncio.run(_seed())
    app = create_app(state_dir=tmp_path)
    try:
        with TestClient(app) as client:
            yield client, {"Authorization": f"Bearer {admin_token}"}
    finally:
        if prev_db is None:
            os.environ.pop("CVCPKG_DATABASE_URL", None)
        else:
            os.environ["CVCPKG_DATABASE_URL"] = prev_db
        if prev_mirror is not None:
            os.environ["CVCPKG_MIRROR_MODE"] = prev_mirror


def _params(name, **extra):
    return {"name": name, "version": "1.0.0+cvc.1", "platform": "linux", "arch": "x86_64", **extra}


def _publish(client, hdr, name, **extra):
    return client.post(
        "/v1/publish",
        params=_params(name, **extra),
        files={"file": (f"{name}.tar.zst", io.BytesIO(b"payload"))},
        headers=hdr,
    )


class TestPublishFieldLengths:
    def test_multi_licence_expression_publishes(self, server_env):
        client, hdr = server_env
        resp = _publish(client, hdr, "coinlike", license=COIN_LICENSE)
        assert resp.status_code == 200, resp.text
        got = client.get("/v1/packages/coinlike").json()["packages"][0]
        assert got["license"] == COIN_LICENSE

    def test_overlong_licence_is_a_422_naming_the_field(self, server_env):
        client, hdr = server_env
        resp = _publish(client, hdr, "toolong", license="MIT AND " * 200)
        assert resp.status_code == 422, resp.text
        assert "license" in resp.json()["detail"]
        assert "1024" in resp.json()["detail"]
        assert client.get("/v1/packages/toolong").json()["total"] == 0

    def test_overlong_homepage_is_a_422(self, server_env):
        client, hdr = server_env
        resp = _publish(client, hdr, "longurl", homepage="https://x.example/" + "a" * 600)
        assert resp.status_code == 422, resp.text
        assert "homepage" in resp.json()["detail"]

    def test_chunked_init_refuses_too(self, server_env):
        client, hdr = server_env
        resp = client.post(
            "/v1/upload/init",
            params=_params("toolong-chunked", license="X" * 1025, total_size=7),
            headers=hdr,
        )
        assert resp.status_code == 422, resp.text
        assert "license" in resp.json()["detail"]
