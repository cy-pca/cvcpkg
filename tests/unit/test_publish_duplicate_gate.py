"""Store-layer duplicate-publish gate.

``DbPackageIndex.add_package`` grew a defense-in-depth check (PR #176) that
raises ``ValueError`` when a row with the same
``(name, version, platform, arch, build_type, link, org_slug)`` already
exists; the publish endpoints translate that into HTTP 409.  That store-layer
guard merged without a unit test — this covers it directly.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiosqlite", reason="aiosqlite required")
pytest.importorskip("sqlalchemy", reason="sqlalchemy required")


def _variant(**over):
    """Baseline add_package kwargs; override to make a distinct variant."""
    kw = dict(
        name="zlib",
        version="1.3.1+cvc.1",
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256="a" * 64,
        size_bytes=123,
        archive_url="file:///tmp/zlib.tar.zst",
    )
    kw.update(over)
    return kw


def test_add_package_rejects_duplicate_variant(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbPackageIndex

    async def _run():
        init_db(db_url)
        await create_tables()
        idx = DbPackageIndex()

        # First insert of the variant succeeds.
        await idx.add_package(**_variant())

        # An identical variant is rejected at the store layer.
        with pytest.raises(ValueError, match="already exists"):
            await idx.add_package(**_variant())

        # A different arch is a *distinct* variant and must still be allowed
        # (guards against the check being too broad).
        await idx.add_package(**_variant(arch="arm64"))

        # And a different org is also distinct.
        await idx.add_package(**_variant(org_slug="acme"))

        await dispose_engine()

    asyncio.run(_run())
