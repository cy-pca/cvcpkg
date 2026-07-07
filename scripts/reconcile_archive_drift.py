#!/usr/bin/env python3
"""Reconcile catalog metadata against the on-disk archive bytes.

Two operating modes, autodetected from ``CVCPKG_MIRROR_MODE``:

**Primary mode** (default; DB is source of truth for what should exist)
  A race in POST /v1/upload/{id}/complete (fixed alongside this script)
  could clobber an existing archive on disk when a second publisher
  completed a chunked upload for an already-published NVR. The rename over
  the destination succeeded, but the follow-up ``add_package`` insert
  failed on the unique index, leaving ``DB.sha256/size_bytes`` out of sync
  with the file downloaders actually receive.

  This tool re-hashes every archive under ``$CVCPKG_STATE_DIR/archives``
  and, for each row whose ``(sha256, size_bytes)`` disagrees with the file
  on disk, updates the DB row to match reality plus records an audit
  entry per fix.

**Mirror mode** (``CVCPKG_MIRROR_MODE=1``; upstream catalog is truth)
  The mirror lazily caches archives fetched via ``/v1/mirror/download``.
  If the upstream primary was drifted at the time bytes were cached, the
  mirror's cached bytes now disagree with the (post-reconcile) upstream
  sha256. Since the mirror does not populate ``PackageRow``, we take the
  in-memory catalog (``$CVCPKG_STATE_DIR/index.yaml``, populated by the
  mirror sync loop) as truth. For each cached archive whose sha256
  disagrees, the file is deleted so the next ``/v1/mirror/download``
  request re-fetches from upstream.

Both modes:

  --dry-run   (default) Print the drift set only.
  --apply     Perform the writes (UPDATE rows / delete stale caches).

The script only ever *shrinks* drift — it never deletes archives from the
primary and never touches DB rows in mirror mode.

Usage from inside the running backend container::

    # Primary (cvcpkg.org)
    docker exec -it cvcpkg-prod-backend \\
        python -m cvcpkg.scripts.reconcile_archive_drift
    docker exec -it cvcpkg-prod-backend \\
        python -m cvcpkg.scripts.reconcile_archive_drift --apply

    # Mirror (pkg.tx.wtf) — restart first to force a fresh catalog sync
    docker compose -f docker-compose.production.yml restart backend
    docker exec -it cvcpkg-prod-backend \\
        python -m cvcpkg.scripts.reconcile_archive_drift
    docker exec -it cvcpkg-prod-backend \\
        python -m cvcpkg.scripts.reconcile_archive_drift --apply
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select, update

from cvcpkg.server.db import PackageRow, dispose_engine, get_session, init_db
from cvcpkg.server.db_stores import DbAuditLog
from cvcpkg.server.models import AuditAction


CHUNK = 4 * 1024 * 1024


def _is_mirror() -> bool:
    return os.environ.get("CVCPKG_MIRROR_MODE", "").lower() in ("1", "true", "yes")


def _sha256_and_size(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(CHUNK), b""):
            h.update(buf)
            size += len(buf)
    return h.hexdigest(), size


@dataclass
class Drift:
    row_id: int
    name: str
    version: str
    platform: str
    arch: str
    build_type: str
    link: str
    archive_url: str
    db_sha: str
    db_size: int
    disk_sha: str
    disk_size: int
    archive_path: Path


async def _collect_drift(archives_dir: Path, verbose: bool) -> list[Drift]:
    drift: list[Drift] = []
    async with get_session() as session:
        result = await session.execute(select(PackageRow))
        rows = result.scalars().all()

    for row in rows:
        filename = row.archive_url.rsplit("/", 1)[-1]
        path = archives_dir / filename
        if not path.exists():
            if verbose:
                print(
                    f"MISSING|{row.name}|{row.version}|{row.platform}|{row.arch}"
                    f"|{row.build_type}|{row.link}|{filename}",
                    flush=True,
                )
            continue
        disk_sha, disk_size = _sha256_and_size(path)
        if disk_sha == row.sha256 and disk_size == row.size_bytes:
            continue
        drift.append(
            Drift(
                row_id=row.id,
                name=row.name,
                version=row.version,
                platform=row.platform,
                arch=row.arch,
                build_type=row.build_type,
                link=row.link,
                archive_url=row.archive_url,
                db_sha=row.sha256,
                db_size=row.size_bytes,
                disk_sha=disk_sha,
                disk_size=disk_size,
                archive_path=path,
            )
        )
    return drift


def _print_report(drift: list[Drift]) -> None:
    if not drift:
        print("no drift detected", flush=True)
        return
    print(
        "id|name|version|platform|arch|build_type|link" "|db_sha256|db_size|disk_sha256|disk_size",
        flush=True,
    )
    for d in drift:
        print(
            f"{d.row_id}|{d.name}|{d.version}|{d.platform}|{d.arch}|{d.build_type}"
            f"|{d.link}|{d.db_sha}|{d.db_size}|{d.disk_sha}|{d.disk_size}",
            flush=True,
        )
    print(f"total drift rows: {len(drift)}", flush=True)


async def _apply(drift: list[Drift], actor: str) -> None:
    async with get_session() as session:
        for d in drift:
            await session.execute(
                update(PackageRow)
                .where(PackageRow.id == d.row_id)
                .values(sha256=d.disk_sha, size_bytes=d.disk_size)
            )
        await session.commit()

    audit = DbAuditLog()
    for d in drift:
        # No dedicated action for reconciliation; catalog_rebuild is the
        # closest existing entry and reads sensibly in the audit trail.
        await audit.record(
            action=AuditAction.catalog_rebuild,
            actor=actor,
            target=f"{d.name}=={d.version}",
            detail=(
                f"reconcile_archive_drift platform={d.platform} arch={d.arch} "
                f"build_type={d.build_type} link={d.link} "
                f"old_sha={d.db_sha} old_size={d.db_size} "
                f"new_sha={d.disk_sha} new_size={d.disk_size}"
            ),
        )


# ── Mirror mode ───────────────────────────────────────────────


@dataclass
class MirrorDrift:
    filename: str
    catalog_sha: str
    catalog_size: int
    disk_sha: str
    disk_size: int
    archive_path: Path


def _load_mirror_catalog(state_dir: Path) -> list[dict]:
    idx_path = state_dir / "index.yaml"
    if not idx_path.is_file():
        return []
    with idx_path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return []
    return list(data.get("bundles", []))


def _collect_mirror_drift(archives_dir: Path, bundles: list[dict]) -> list[MirrorDrift]:
    """Compare each cached archive against the (post-sync) mirror catalog."""
    by_filename: dict[str, dict] = {}
    for b in bundles:
        url = b.get("archive_url") or ""
        filename = url.rsplit("/", 1)[-1]
        if filename:
            by_filename[filename] = b

    drift: list[MirrorDrift] = []
    for path in sorted(archives_dir.iterdir()):
        # Skip in-flight downloads written by the mirror proxy
        if path.suffix == ".downloading" or not path.is_file():
            continue
        bundle = by_filename.get(path.name)
        if bundle is None:
            # Cached archive with no matching catalog entry — likely yanked
            # upstream. Report but don't act on it here.
            continue
        cat_sha = str(bundle.get("sha256", ""))
        cat_size = int(bundle.get("size_bytes", 0))
        disk_sha, disk_size = _sha256_and_size(path)
        if disk_sha == cat_sha and disk_size == cat_size:
            continue
        drift.append(
            MirrorDrift(
                filename=path.name,
                catalog_sha=cat_sha,
                catalog_size=cat_size,
                disk_sha=disk_sha,
                disk_size=disk_size,
                archive_path=path,
            )
        )
    return drift


def _print_mirror_report(drift: list[MirrorDrift]) -> None:
    if not drift:
        print("no mirror-cache drift detected", flush=True)
        return
    print("filename|catalog_sha256|catalog_size|disk_sha256|disk_size", flush=True)
    for d in drift:
        print(
            f"{d.filename}|{d.catalog_sha}|{d.catalog_size}" f"|{d.disk_sha}|{d.disk_size}",
            flush=True,
        )
    print(f"total stale cached archives: {len(drift)}", flush=True)


def _apply_mirror(drift: list[MirrorDrift]) -> None:
    """Delete stale cached archives so the next request re-fetches upstream."""
    for d in drift:
        try:
            d.archive_path.unlink()
        except FileNotFoundError:
            pass


async def _main_async(args: argparse.Namespace) -> int:
    state_dir = Path(os.environ.get("CVCPKG_STATE_DIR", "/app/data"))
    archives_dir = state_dir / "archives"
    if not archives_dir.is_dir():
        print(f"archives dir does not exist: {archives_dir}", file=sys.stderr)
        return 2

    if _is_mirror():
        print("mode: mirror (catalog = truth, cached archives verified)", flush=True)
        bundles = _load_mirror_catalog(state_dir)
        if not bundles:
            print(
                f"mirror catalog empty at {state_dir / 'index.yaml'}; "
                "wait for or force a catalog sync before running this",
                file=sys.stderr,
            )
            return 2
        drift = _collect_mirror_drift(archives_dir, bundles)
        _print_mirror_report(drift)
        if not drift:
            return 0
        if args.apply:
            _apply_mirror(drift)
            print(
                f"deleted {len(drift)} stale cached archive(s); "
                "next download request will re-fetch from upstream",
                flush=True,
            )
        else:
            print("dry-run: no files deleted. rerun with --apply to fix.", flush=True)
        return 0

    # Primary mode
    print("mode: primary (DB = truth, on-disk sha adopted)", flush=True)
    db_url = os.environ.get("CVCPKG_DATABASE_URL")
    if not db_url:
        print("CVCPKG_DATABASE_URL must be set in primary mode", file=sys.stderr)
        return 2

    init_db(db_url)
    try:
        drift = await _collect_drift(archives_dir, verbose=args.verbose)
        _print_report(drift)
        if not drift:
            return 0
        if args.apply:
            await _apply(drift, actor=args.actor)
            print(f"applied {len(drift)} DB reconciliations", flush=True)
        else:
            print("dry-run: no changes made. rerun with --apply to fix.", flush=True)
        return 0
    finally:
        await dispose_engine()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Perform UPDATEs. Without this, script only prints drift set.",
    )
    p.add_argument(
        "--actor",
        default="reconcile-script",
        help="Actor name recorded in the audit log for each update.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Also report DB rows whose archive file is missing on disk.",
    )
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
