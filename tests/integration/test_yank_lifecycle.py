"""Integration tests for the cvcpkg yank / retention / nuke lifecycle.

Three layers, fastest first:

  * STORE-LEVEL (temp sqlite, ``DbPackageIndex`` + ``archive_store`` directly):
    the retention/purge/nuke semantics that never need a running server.
  * ENDPOINT-LEVEL (DB-backed ``TestClient``): RBAC, ownership, the org
    storage-counter decrement, and the audit trail — where only the endpoint
    matters and a full uvicorn process would be wasteful.
  * LIVE-SERVER + REAL CLI (``lab/federation_lab.Server`` + ``click`` runner):
    the end-to-end yank-is-deletion-from-view flow, the search renderer, and
    the CLI-preview / server-count agreement that the in-process fakes cannot
    structurally exercise.

Time-travel is done the way ``tests/unit/test_retention.py`` does it — writing
the aged timestamp straight into the row via ``get_session`` — never freezegun.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import pathlib
import re
import sys

import pytest

# Server extras gate — mirrors tests/unit/test_retention.py and
# tests/integration/test_federation_lab.py.
fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for the DB backend")
httpx = pytest.importorskip("httpx", reason="httpx required")
pytest.importorskip("uvicorn", reason="uvicorn required to run real servers")

from cvcpkg.server import archive_store
from cvcpkg.server.models import TokenRole

REPO = pathlib.Path(__file__).resolve().parents[2]

# Same sanitiser the publish endpoint applies to derive an archive filename
# (app.py: ``safe_filename``).
_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+")


def _fname(name: str, version: str, platform: str, arch: str, build_type: str, link: str) -> str:
    raw = f"{name}-{version}-{platform}-{arch}-{build_type}-{link}.tar.zst"
    return "".join(c if c in _SAFE else "_" for c in raw)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _ago(days: int) -> datetime.datetime:
    return _now() - datetime.timedelta(days=days)


# ────────────────────────────────────────────────────────────────
# STORE-LEVEL: DbPackageIndex + archive_store directly (temp sqlite)
# ────────────────────────────────────────────────────────────────


class TestYankRetentionStore:
    """purge_yanked / nuke_bundles / yank clock semantics, archive bytes and all."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'yank_store.db'}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
        monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

        from cvcpkg.server.db import create_tables, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        self._tmp = tmp_path
        self.storage_uri = str(tmp_path / "storage")
        yield

        from cvcpkg.server.db import dispose_engine

        asyncio.run(dispose_engine())

    # ── helpers ──────────────────────────────────────────────────

    def _run(self, coro):
        return asyncio.run(coro)

    async def _add(
        self,
        idx,
        *,
        name="widget",
        version="1.0.0",
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        org_slug="",
        release_tag="",
        content=b"archive-bytes",
        archive_url=None,
        write_archive=True,
    ) -> str:
        """Add a package row and (optionally) drop its archive on disk.

        Returns the archive filename so the caller can assert on archive_store.
        """
        if archive_url is None:
            fname = _fname(name, version, platform, arch, build_type, link)
            archive_url = f"/v1/download/{fname}"
        else:
            fname = archive_url.rsplit("/", 1)[-1]

        if write_archive:
            root = pathlib.Path(self.storage_uri) / "archives"
            root.mkdir(parents=True, exist_ok=True)
            (root / fname).write_bytes(content)

        await idx.add_package(
            name=name,
            version=version,
            platform=platform,
            arch=arch,
            build_type=build_type,
            link=link,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            archive_url=archive_url,
            release_tag=release_tag,
            org_slug=org_slug,
        )
        return fname

    async def _set_row(self, name, version, *, arch=None, **fields):
        """Overwrite arbitrary columns on the matching row(s) (time-travel)."""
        from sqlalchemy import select

        from cvcpkg.server.db import PackageRow, get_session

        async with get_session() as session:
            q = select(PackageRow).where(PackageRow.name == name, PackageRow.version == version)
            if arch is not None:
                q = q.where(PackageRow.arch == arch)
            for row in (await session.execute(q)).scalars().all():
                for k, v in fields.items():
                    setattr(row, k, v)

    async def _read_yanked_at(self, name, version, *, arch=None):
        from sqlalchemy import select

        from cvcpkg.server.db import PackageRow, get_session

        async with get_session() as session:
            q = select(PackageRow).where(PackageRow.name == name, PackageRow.version == version)
            if arch is not None:
                q = q.where(PackageRow.arch == arch)
            row = (await session.execute(q)).scalars().first()
            return None if row is None else (row.yanked, row.yanked_at)

    # ── 1. aged yanked row purged AND its archive is gone ────────

    def test_01_purge_removes_row_and_archive_bytes(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(400))
            assert archive_store.exists(self.storage_uri, fname) is True

            purged = await idx.purge_yanked(
                older_than_days=90, storage_uri=self.storage_uri, dry_run=False
            )
            assert len(purged) == 1
            assert purged[0]["archive_deleted"] is True
            # The row is gone …
            pkgs, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert pkgs == []
            # … and, crucially, the archive bytes are physically gone.
            assert archive_store.exists(self.storage_uri, fname) is False

        self._run(_t())

    # ── 2. row yanked 30d survives a 90d window (cutoff sign) ────

    def test_02_recent_yank_survives_window(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(30))

            purged = await idx.purge_yanked(
                older_than_days=90, storage_uri=self.storage_uri, dry_run=False
            )
            assert purged == []
            pkgs, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert len(pkgs) == 1
            assert archive_store.exists(self.storage_uri, fname) is True

        self._run(_t())

    # ── 3. active row is never purged (retention targets yanked) ─

    def test_03_active_row_untouched(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            # Active (never yanked). Age published_at into the far past to be
            # sure it is age, not yank-state, that would trigger a purge.
            await self._set_row("widget", "1.0.0", published_at=_ago(400))

            purged = await idx.purge_yanked(
                older_than_days=1, storage_uri=self.storage_uri, dry_run=False
            )
            assert purged == []
            pkgs, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert len(pkgs) == 1
            assert pkgs[0].yanked is False
            assert archive_store.exists(self.storage_uri, fname) is True

        self._run(_t())

    # ── 4. yanked_at IS NULL is never purged (no-backfill decision) ─

    def test_04_yanked_at_null_is_exempt(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            # Simulate a row yanked BEFORE migration 017 existed: yanked True
            # but yanked_at NULL. Retention must treat NULL as "never purge".
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=None)

            purged = await idx.purge_yanked(
                older_than_days=1, storage_uri=self.storage_uri, dry_run=False
            )
            assert purged == []
            state = await self._read_yanked_at("widget", "1.0.0")
            assert state == (True, None)
            assert archive_store.exists(self.storage_uri, fname) is True

        self._run(_t())

    # ── 5. re-yank does NOT reset yanked_at (coalesce) ───────────

    def test_05_reyank_does_not_reset_clock(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await self._add(idx, arch="x86_64")
            await self._add(idx, arch="arm64")

            # First yank: a narrow scope, one variant.
            await idx.yank("widget", "1.0.0", arch="x86_64")
            # Pin the clock to a deterministic far-past value so a reset would
            # be unmistakable, then read it straight back from the DB.
            await self._set_row("widget", "1.0.0", arch="x86_64", yanked_at=_ago(500))
            _, pinned = await self._read_yanked_at("widget", "1.0.0", arch="x86_64")
            assert pinned is not None

            # Re-yank WIDER (whole version, no scope) — this UPDATE also matches
            # the already-yanked x86_64 row. coalesce must keep its old clock.
            await idx.yank("widget", "1.0.0")

            _, after = await self._read_yanked_at("widget", "1.0.0", arch="x86_64")
            assert after == pinned, "re-yank reset yanked_at (coalesce broken)"

        self._run(_t())

    # ── 6. unyank clears yanked_at back to NULL ──────────────────

    def test_06_unyank_clears_clock(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await self._add(idx)
            await idx.yank("widget", "1.0.0")
            yanked, at = await self._read_yanked_at("widget", "1.0.0")
            assert yanked is True and at is not None

            await idx.unyank("widget", "1.0.0")
            yanked, at = await self._read_yanked_at("widget", "1.0.0")
            assert yanked is False
            assert at is None

        self._run(_t())

    # ── 7. tagged release is exempt from retention ───────────────

    def test_07_tagged_release_not_purged(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx, release_tag="v1.0.0")
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(400))

            purged = await idx.purge_yanked(
                older_than_days=90, storage_uri=self.storage_uri, dry_run=False
            )
            assert purged == []
            pkgs, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert len(pkgs) == 1
            assert archive_store.exists(self.storage_uri, fname) is True

        self._run(_t())

    # ── 9. shared archive: nuking one of a pair keeps the file ───

    def test_09_nuke_refcount_guard_keeps_shared_archive(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            shared_url = "/v1/download/shared-blob.tar.zst"
            fname = await self._add(idx, arch="x86_64", archive_url=shared_url)
            # Second variant references the SAME archive filename.
            await self._add(idx, arch="arm64", archive_url=shared_url, write_archive=False)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(1))
            assert archive_store.exists(self.storage_uri, fname) is True

            result = await idx.nuke_bundles(
                "widget", "1.0.0", arch="x86_64", storage_uri=self.storage_uri
            )
            assert result["count"] == 1
            assert result["nuked"][0]["archive_deleted"] is False
            # The arm64 sibling still references the file, so it MUST survive.
            assert archive_store.exists(self.storage_uri, fname) is True
            survivors, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert {p.arch for p in survivors} == {"arm64"}

        self._run(_t())

    # ── 14. nuke require_yanked refuses a live variant (ValueError) ─

    def test_14_nuke_requires_yanked_names_live_variant(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await self._add(idx)  # active, not yanked

            with pytest.raises(ValueError) as exc:
                await idx.nuke_bundles(
                    "widget", "1.0.0", require_yanked=True, storage_uri=self.storage_uri
                )
            # The message must name the live variant so the operator knows what
            # to yank first.
            assert "linux/x86_64/release/shared" in str(exc.value)
            # Nothing was destroyed.
            pkgs, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert len(pkgs) == 1

        self._run(_t())

    # ── 15. scoped nuke leaves the other arch alive ─────────────

    def test_15_scoped_nuke_leaves_sibling_arch(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            f_x = await self._add(idx, arch="x86_64")
            f_a = await self._add(idx, arch="arm64")
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(1))

            result = await idx.nuke_bundles(
                "widget", "1.0.0", arch="x86_64", storage_uri=self.storage_uri
            )
            assert result["count"] == 1

            survivors, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert [p.arch for p in survivors] == ["arm64"]
            # x86_64 archive gone, arm64 archive intact.
            assert archive_store.exists(self.storage_uri, f_x) is False
            assert archive_store.exists(self.storage_uri, f_a) is True

        self._run(_t())

    # ── 16. nuke removes row AND archive (nuke != yank) ─────────

    def test_16_nuke_removes_row_and_archive(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(1))

            result = await idx.nuke_bundles("widget", "1.0.0", storage_uri=self.storage_uri)
            assert result["count"] == 1
            assert result["nuked"][0]["archive_deleted"] is True
            pkgs, _ = await idx.get_bundles(name="widget", include_yanked=True)
            assert pkgs == []
            assert archive_store.exists(self.storage_uri, fname) is False

        self._run(_t())

    # ── 20. manual nuke writes a tombstone with reason=manual ────

    def test_20_manual_nuke_tombstone_reason_and_actor(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(1))
            await idx.nuke_bundles(
                "widget",
                "1.0.0",
                storage_uri=self.storage_uri,
                nuked_by="alice-admin",
                reason="manual",
            )
            ts = await idx.get_tombstones("widget")
            assert len(ts) == 1
            assert ts[0]["reason"] == "manual"
            assert ts[0]["nuked_by"] == "alice-admin"
            # forensic context is copied off the row before it's deleted
            assert ts[0]["sha256"] == hashlib.sha256(b"archive-bytes").hexdigest()
            assert ts[0]["filename"] == fname
            assert ts[0]["yanked_at"] is not None

        self._run(_t())

    # ── 21. retention purge writes reason=retention / retention-gc ─

    def test_21_retention_purge_tombstone_reason(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await self._add(idx)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(400))
            await idx.purge_yanked(older_than_days=90, storage_uri=self.storage_uri)
            ts = await idx.get_tombstones("widget")
            assert len(ts) == 1
            assert ts[0]["reason"] == "retention"
            assert ts[0]["nuked_by"] == "retention-gc"

        self._run(_t())

    # ── 22. a dry-run purge writes NO tombstone ──────────────────

    def test_22_dry_run_writes_no_tombstone(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            await self._add(idx)
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(400))
            await idx.purge_yanked(older_than_days=90, storage_uri=self.storage_uri, dry_run=True)
            assert await idx.get_tombstones("widget") == []

        self._run(_t())

    # ── 23. tombstone reachable BY FILENAME (drives the 410) ─────

    def test_23_tombstone_lookup_by_filename(self):
        from cvcpkg.server.db_stores import DbPackageIndex

        async def _t():
            idx = DbPackageIndex()
            fname = await self._add(idx)
            assert await idx.get_tombstone_by_filename(fname) is None
            await self._set_row("widget", "1.0.0", yanked=True, yanked_at=_ago(1))
            await idx.nuke_bundles("widget", "1.0.0", storage_uri=self.storage_uri)
            hit = await idx.get_tombstone_by_filename(fname)
            assert hit is not None and hit["filename"] == fname

        self._run(_t())


# ────────────────────────────────────────────────────────────────
# ENDPOINT-LEVEL: DB-backed TestClient (RBAC / ownership / storage / audit)
# ────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """DB-backed TestClient with admin + two publisher tokens.

    Mirrors ``tests/unit/test_retention.py::db_server_env`` (same seeding and
    same ``DbTokenStore``/``create_app`` state-dir sharing so minted tokens
    verify), extended with a second publisher for the ownership test.
    """
    from fastapi.testclient import TestClient

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.app import create_app
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin = await store.create("yank-admin", TokenRole.admin)
        pub_a = await store.create("pub-a", TokenRole.publisher)
        pub_b = await store.create("pub-b", TokenRole.publisher)
        await dispose_engine()
        return admin, pub_a, pub_b

    admin_token, pub_a_token, pub_b_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_a_token, pub_b_token, tmp_path


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _publish_client(
    client,
    token,
    *,
    name,
    version="1.0.0",
    platform="linux",
    arch="x86_64",
    build_type="release",
    link="shared",
    org="",
    content=b"endpoint-archive",
):
    params = {
        "name": name,
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": build_type,
        "link": link,
    }
    if org:
        params["org"] = org
    return client.post(
        "/v1/publish",
        params=params,
        files={"file": (f"{name}.tar.zst", content, "application/octet-stream")},
        headers=_hdr(token),
    )


def _age_yanked_at(name, version, days):
    """Push a package's yanked_at into the past, in-process (test_retention style)."""
    from sqlalchemy import select

    from cvcpkg.server.db import PackageRow, get_session

    async def _t():
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(PackageRow).where(
                            PackageRow.name == name, PackageRow.version == version
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.yanked_at = _ago(days)

    asyncio.run(_t())


# ── 8. purge decrements org storage_used_bytes by exactly size_bytes ──


def test_08_purge_decrements_org_storage_once(db_env):
    """Publish into an org, yank, purge → the org counter falls by size_bytes.

    The store method returns the dicts; the ADMIN gc/yanked endpoint owns the
    decrement, so this exercises the endpoint (still a fast in-process client,
    no uvicorn) and reads the counter back through DbOrgStore.
    """
    client, admin, _pa, _pb, tmp_path = db_env
    from cvcpkg.server.db_stores import DbOrgStore

    r = client.post(
        "/v1/orgs",
        json={"slug": "acme", "display_name": "Acme", "is_private": False},
        headers=_hdr(admin),
    )
    assert r.status_code == 200, r.text

    content = b"x" * 4096
    r = _publish_client(client, admin, name="orgpkg", org="acme", content=content)
    assert r.status_code == 200, r.text

    used_after_publish = asyncio.run(DbOrgStore().get("acme")).storage_used_bytes
    assert used_after_publish == len(content)

    r = client.post("/v1/packages/orgpkg/1.0.0/yank", headers=_hdr(admin))
    assert r.status_code == 200

    _age_yanked_at("orgpkg", "1.0.0", days=10)

    r = client.post(
        "/v1/admin/gc/yanked",
        params={"older_than_days": 1, "dry_run": False},
        headers=_hdr(admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1

    used_after_purge = asyncio.run(DbOrgStore().get("acme")).storage_used_bytes
    assert used_after_purge == used_after_publish - len(content) == 0


# ── 10. a real purge writes an audit "nuke" row (actor = admin) ──


def test_10_purge_writes_audit_nuke_row(db_env):
    client, admin, _pa, _pb, _tmp = db_env

    r = _publish_client(client, admin, name="auditpkg")
    assert r.status_code == 200, r.text
    r = client.post("/v1/packages/auditpkg/1.0.0/yank", headers=_hdr(admin))
    assert r.status_code == 200

    _age_yanked_at("auditpkg", "1.0.0", days=10)

    r = client.post(
        "/v1/admin/gc/yanked",
        params={"older_than_days": 1, "dry_run": False},
        headers=_hdr(admin),
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # An audit row with action "nuke" and actor = the admin token exists.
    r = client.get("/v1/audit", params={"action": "nuke", "limit": 1000}, headers=_hdr(admin))
    assert r.status_code == 200
    nuke_rows = [e for e in r.json()["entries"] if e["action"] == "nuke"]
    assert nuke_rows, "no audit row with action=nuke after a real purge"
    assert any(e["actor"] == "yank-admin" for e in nuke_rows)

    # And the audit chain still verifies cleanly.
    r = client.get("/v1/audit/verify", headers=_hdr(admin))
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── 11. gc/yanked is admin-only ──────────────────────────────────


def test_11_gc_yanked_rbac(db_env):
    client, admin, pub_a, _pb, _tmp = db_env
    forbidden = client.post(
        "/v1/admin/gc/yanked",
        params={"older_than_days": 1, "dry_run": True},
        headers=_hdr(pub_a),
    )
    assert forbidden.status_code == 403
    allowed = client.post(
        "/v1/admin/gc/yanked",
        params={"older_than_days": 1, "dry_run": True},
        headers=_hdr(admin),
    )
    assert allowed.status_code == 200


# ── 12. nuke is admin-only (publisher 403, admin 200 on a yanked bundle) ──


def test_12_nuke_rbac(db_env):
    client, admin, pub_a, _pb, _tmp = db_env

    # pub-a publishes and yanks its own bundle.
    assert _publish_client(client, pub_a, name="nukepkg").status_code == 200
    assert client.post("/v1/packages/nukepkg/1.0.0/yank", headers=_hdr(pub_a)).status_code == 200

    # Publisher may not nuke.
    forbidden = client.post("/v1/packages/nukepkg/1.0.0/nuke", headers=_hdr(pub_a))
    assert forbidden.status_code == 403

    # Admin may.
    allowed = client.post("/v1/packages/nukepkg/1.0.0/nuke", headers=_hdr(admin))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["count"] == 1


# ── 24. after nuke: download is 410 Gone, and the tombstone API explains it ──


def test_24_nuked_download_is_410_and_tombstone_api(db_env):
    client, admin, pub_a, _pb, _tmp = db_env

    assert _publish_client(client, pub_a, name="gonepkg").status_code == 200
    # the archive is downloadable while it exists
    dl_before = client.get("/v1/download/gonepkg-1.0.0-linux-x86_64-release-shared.tar.zst")
    assert dl_before.status_code == 200, dl_before.text

    assert client.post("/v1/packages/gonepkg/1.0.0/yank", headers=_hdr(pub_a)).status_code == 200
    assert client.post("/v1/packages/gonepkg/1.0.0/nuke", headers=_hdr(admin)).status_code == 200

    # Downloading the purged archive now says 410 Gone with the reason, not 404.
    dl = client.get("/v1/download/gonepkg-1.0.0-linux-x86_64-release-shared.tar.zst")
    assert dl.status_code == 410, dl.text
    detail = dl.json()["detail"]
    assert "nuked" in detail and "manual" in detail and "gonepkg==1.0.0" in detail

    # A never-published archive is still a plain 404 (not a false 410).
    assert (
        client.get("/v1/download/neverpkg-9.9-linux-x86_64-release-shared.tar.zst").status_code
        == 404
    )

    # The tombstone API records reason=manual and who did it.
    ts = client.get("/v1/packages/gonepkg/tombstones").json()
    assert ts["count"] == 1
    assert ts["tombstones"][0]["reason"] == "manual"
    assert ts["tombstones"][0]["nuked_by"] == "yank-admin"


# ── 13. a publisher cannot yank another publisher's package ──────


def test_13_yank_ownership_enforced(db_env):
    client, _admin, pub_a, pub_b, _tmp = db_env

    # pub-a publishes (published_by = "pub-a", no org).
    assert _publish_client(client, pub_a, name="ownpkg").status_code == 200

    # pub-b is neither owner nor an org member → 403.
    r = client.post("/v1/packages/ownpkg/1.0.0/yank", headers=_hdr(pub_b))
    assert r.status_code == 403

    # The rightful owner can.
    r = client.post("/v1/packages/ownpkg/1.0.0/yank", headers=_hdr(pub_a))
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────
# LIVE-SERVER + REAL CLI: uvicorn subprocess driven by the click CLI
# ────────────────────────────────────────────────────────────────


def _import_lab_server():
    lab = str(REPO / "lab")
    if lab not in sys.path:
        sys.path.insert(0, lab)
    from federation_lab import Server  # noqa: WPS433

    return Server


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    Server = _import_lab_server()  # noqa: N806 -- holds the lab Server class
    tmp = tmp_path_factory.mktemp("yank-live")
    srv = Server("yanksrv", tmp)
    try:
        srv.bootstrap()
        srv.start()
    except Exception as exc:  # pragma: no cover - environment-dependent
        srv.stop()
        pytest.skip(f"could not launch live cvcpkg-server: {exc}")
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture(scope="module")
def live_pub_token(live_server):
    r = httpx.post(
        f"{live_server.url}/v1/tokens",
        headers=live_server.h(),
        json={"name": "yank-pub", "role": "publisher"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


def _live_publish(
    server,
    token,
    *,
    name,
    version="1.0.0",
    platform="linux",
    arch="x86_64",
    build_type="release",
    link="shared",
    content=b"yank-live-artifact",
):
    r = httpx.post(
        f"{server.url}/v1/publish",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "name": name,
            "version": version,
            "platform": platform,
            "arch": arch,
            "build_type": build_type,
            "link": link,
        },
        files={"file": (f"{name}.tar.zst", content, "application/octet-stream")},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _catalog_bundles(server, name):
    r = httpx.get(f"{server.url}/v1/catalog", timeout=10)
    r.raise_for_status()
    return [b for b in r.json().get("bundles", []) if b.get("name") == name]


def _runner():
    from click.testing import CliRunner

    return CliRunner()


# ── 17. `cvcpkg search --include-yanked` distinguishes yanked from active ──


def test_17_search_include_yanked_distinguishes_state(live_server, live_pub_token):
    from cvcpkg.cli import cli

    name = "searchpkg"
    _live_publish(live_server, live_pub_token, name=name, arch="x86_64")
    _live_publish(live_server, live_pub_token, name=name, arch="arm64")

    # Yank ONLY the x86_64 variant; arm64 stays active.
    r = httpx.post(
        f"{live_server.url}/v1/packages/{name}/1.0.0/yank",
        headers={"Authorization": f"Bearer {live_pub_token}"},
        params={"arch": "x86_64"},
        timeout=10,
    )
    r.raise_for_status()

    result = _runner().invoke(
        cli,
        ["search", name, "--include-yanked", "--server", live_server.url],
    )
    assert result.exit_code == 0, result.output
    out = result.output

    # A State column must appear, and the two variants must be visually
    # distinguishable: the x86_64 row reads "yanked", the arm64 row "active".
    assert "State" in out
    x86_line = next((ln for ln in out.splitlines() if "x86_64" in ln), "")
    arm_line = next((ln for ln in out.splitlines() if "arm64" in ln), "")
    assert "yanked" in x86_line, f"x86_64 row not marked yanked:\n{out}"
    assert "active" in arm_line, f"arm64 row not marked active:\n{out}"


# ── 18. full CLI e2e: yank = deletion-from-view; unyank RBAC; resolve breaks ──


def test_18_cli_yank_unyank_full_cycle(live_server, live_pub_token, tmp_path):
    from cvcpkg.cli import cli

    name = "cyclepkg"
    _live_publish(live_server, live_pub_token, name=name, arch="x86_64")
    _live_publish(live_server, live_pub_token, name=name, arch="arm64")
    assert len(_catalog_bundles(live_server, name)) == 2

    admin_tok = live_server.admin
    catalog_url = f"{live_server.url}/v1/catalog"
    runner = _runner()

    # Control: BEFORE yank the pin resolves against the catalog (proves the
    # platform tuple matches, so a later failure is really the yank).
    pre = runner.invoke(
        cli,
        [
            "install",
            f"{name}==1.0.0",
            "--catalog",
            catalog_url,
            "--source",
            "server",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--prefix",
            str(tmp_path / "pre"),
        ],
    )
    assert "resolved 1 component(s)" in pre.output, pre.output

    # Publisher yanks the whole version (owns it) — CLI reports 2 bundles.
    yanked = runner.invoke(
        cli,
        ["yank", name, "1.0.0", "--server", live_server.url, "--token", live_pub_token, "--yes"],
    )
    assert yanked.exit_code == 0, yanked.output
    assert "(2 bundle(s))" in yanked.output

    # Catalog now shows nothing for this package.
    assert _catalog_bundles(live_server, name) == []

    # The important assertion: an exact pin no longer resolves — yank is
    # deletion-from-view, so the version is simply gone.
    post = runner.invoke(
        cli,
        [
            "install",
            f"{name}==1.0.0",
            "--catalog",
            catalog_url,
            "--source",
            "server",
            "--platform",
            "linux",
            "--arch",
            "x86_64",
            "--prefix",
            str(tmp_path / "post"),
        ],
    )
    assert post.exit_code != 0
    assert "no bundles found in catalog" in post.output, post.output

    # A publisher cannot unyank — admin-only (server returns 403).
    denied = runner.invoke(
        cli,
        ["unyank", name, "1.0.0", "--server", live_server.url, "--token", live_pub_token, "--yes"],
    )
    assert denied.exit_code != 0
    assert "403" in denied.output, denied.output

    # Admin unyank restores all variants.
    restored = runner.invoke(
        cli,
        ["unyank", name, "1.0.0", "--server", live_server.url, "--token", admin_tok, "--yes"],
    )
    assert restored.exit_code == 0, restored.output
    assert "(2 bundle(s))" in restored.output
    assert len(_catalog_bundles(live_server, name)) == 2


# ── 19. CLI yank preview count == server-returned count (drift guard) ──


def test_19_cli_preview_count_matches_server(live_server, live_pub_token):
    from cvcpkg.cli import cli

    name = "previewpkg"
    _live_publish(live_server, live_pub_token, name=name, arch="x86_64")
    _live_publish(live_server, live_pub_token, name=name, arch="arm64")

    result = _runner().invoke(
        cli,
        ["yank", name, "1.0.0", "--server", live_server.url, "--token", live_pub_token, "--yes"],
    )
    assert result.exit_code == 0, result.output
    out = result.output

    # Preview: one variant row per matched bundle ("plat/arch/build/link  state").
    preview_rows = [
        ln for ln in out.splitlines() if re.match(r"\s+\S+/\S+/\S+/\S+\s+(active|yanked)", ln)
    ]
    preview_count = len(preview_rows)

    # Server: the count in the final line.
    m = re.search(r"\((\d+) bundle\(s\)\)", out)
    assert m, f"no server count line:\n{out}"
    server_count = int(m.group(1))

    assert (
        preview_count == server_count == 2
    ), f"preview predicted {preview_count} but server acted on {server_count}\n{out}"
