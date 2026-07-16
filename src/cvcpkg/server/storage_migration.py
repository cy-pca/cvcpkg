"""Offline migration of a cvcpkg server's archive store between backends.

``cvcpkg-server storage migrate --to <uri>`` copies every catalog-referenced
archive from the server's CURRENT storage backend to a new one (e.g.
``file://`` → ``s3://`` on a Garage cluster), verifying SHA-256 integrity on
both the read (source bytes match the catalog) and the write (bytes that
landed in the destination match the catalog), recording a resumable journal,
and — only once every archive has copied and verified — flipping the server's
active ``storage_uri`` to the new backend so it serves from there after a
restart.

Run it with the server stopped (quiesced): it is a filesystem / object-store
operation over the state dir, not an online request path.  If it is
interrupted, re-running with ``--resume`` (the default) skips archives already
verified in the journal and continues.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cvcpkg.server import archive_store

JOURNAL_FILE = "migration-journal.yaml"

# Journal per-archive states.
PENDING = "pending"
VERIFIED = "verified"
FAILED = "failed"


class MigrationError(Exception):
    """A single archive could not be migrated (integrity or I/O failure)."""


# ── Catalog enumeration (shared with the doctor) ────────────────


@dataclass(frozen=True)
class ArchiveRef:
    """One catalog-referenced archive: its filename and expected SHA-256."""

    filename: str
    sha256: str
    size: int = -1


def _filename_of(archive_url: str) -> str:
    """Extract the stored archive filename from an ``archive_url``.

    ``archive_url`` is typically ``/v1/download/<filename>`` but may be a full
    URL; take the last path segment either way.
    """
    if not archive_url:
        return ""
    return archive_url.rstrip("/").rsplit("/", 1)[-1]


def _yaml_archives(state_dir: Path) -> list[ArchiveRef]:
    idx = Path(state_dir) / "index.yaml"
    refs: dict[str, ArchiveRef] = {}
    if idx.is_file():
        data = yaml.safe_load(idx.read_text()) or {}
        for b in data.get("bundles", []):
            fn = _filename_of(str(b.get("archive_url", "")))
            if not fn:
                continue
            try:
                size = int(b.get("size_bytes", -1))
            except (TypeError, ValueError):
                size = -1
            refs[fn] = ArchiveRef(fn, str(b.get("sha256", "")), size)
    return list(refs.values())


def _db_archives(database_url: str) -> list[ArchiveRef]:
    # Imported lazily so YAML-mode migrations need neither the async DB stack
    # nor the server extras.
    import asyncio

    from cvcpkg.server.db import dispose_engine, init_db
    from cvcpkg.server.db_stores import DbPackageIndex

    init_db(database_url)

    async def _query() -> list[ArchiveRef]:
        idx = DbPackageIndex()
        bundles, _ = await idx.get_bundles(include_yanked=True, limit=1_000_000, offset=0)
        refs: dict[str, ArchiveRef] = {}
        for b in bundles:
            fn = _filename_of(str(getattr(b, "archive_url", "")))
            if not fn:
                continue
            size = int(getattr(b, "size", getattr(b, "size_bytes", -1)) or -1)
            refs[fn] = ArchiveRef(fn, str(getattr(b, "sha256", "")), size)
        return list(refs.values())

    try:
        return asyncio.run(_query())
    finally:
        try:
            asyncio.run(dispose_engine())
        except Exception:
            pass


def iter_catalog_archives(state_dir: Path, database_url: str = "") -> list[ArchiveRef]:
    """Enumerate every archive the catalog references (yanked included).

    Uses the SQL catalog when *database_url* is given, else the YAML index in
    *state_dir*.  Deduplicated by filename.
    """
    if database_url:
        return _db_archives(database_url)
    return _yaml_archives(state_dir)


# ── Resumable journal ───────────────────────────────────────────


@dataclass
class MigrationJournal:
    """Persisted record of a migration's per-archive progress.

    Written to ``<state-dir>/migration-journal.yaml`` after every archive so an
    interrupted run resumes without recopying verified archives, and so the
    doctor can detect an incomplete migration.
    """

    state_dir: Path
    source_uri: str
    dest_uri: str
    entries: dict[str, dict] = field(default_factory=dict)
    completed: bool = False

    @property
    def path(self) -> Path:
        return Path(self.state_dir) / JOURNAL_FILE

    @classmethod
    def load(cls, state_dir: Path) -> MigrationJournal | None:
        p = Path(state_dir) / JOURNAL_FILE
        if not p.is_file():
            return None
        data = yaml.safe_load(p.read_text()) or {}
        return cls(
            state_dir=Path(state_dir),
            source_uri=str(data.get("source_uri", "")),
            dest_uri=str(data.get("dest_uri", "")),
            entries=dict(data.get("entries", {}) or {}),
            completed=bool(data.get("completed", False)),
        )

    @classmethod
    def load_or_new(cls, state_dir: Path, source_uri: str, dest_uri: str) -> MigrationJournal:
        existing = cls.load(state_dir)
        if existing is not None and existing.dest_uri == dest_uri:
            existing.source_uri = source_uri or existing.source_uri
            return existing
        # A journal for a *different* destination is stale — start fresh.
        return cls(state_dir=Path(state_dir), source_uri=source_uri, dest_uri=dest_uri)

    def save(self) -> None:
        Path(self.state_dir).mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(
                {
                    "source_uri": self.source_uri,
                    "dest_uri": self.dest_uri,
                    "completed": self.completed,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "entries": self.entries,
                },
                default_flow_style=False,
                sort_keys=True,
            )
        )

    def mark(self, filename: str, status: str, *, sha256: str = "", error: str = "") -> None:
        self.entries[filename] = {"status": status, "sha256": sha256, "error": error}

    def is_verified(self, filename: str) -> bool:
        return self.entries.get(filename, {}).get("status") == VERIFIED

    def pending_or_failed(self) -> list[str]:
        return [fn for fn, e in self.entries.items() if e.get("status") in (PENDING, FAILED)]


# ── Result ──────────────────────────────────────────────────────


@dataclass
class MigrationResult:
    total: int = 0
    migrated: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    flipped: bool = False
    source_uri: str = ""
    dest_uri: str = ""

    @property
    def ok(self) -> bool:
        return not self.failures


# ── Migration driver ────────────────────────────────────────────


def run_migration(
    state_dir: Path,
    dest_uri: str,
    *,
    database_url: str = "",
    resume: bool = True,
    deep_verify: bool = True,
    log: Callable[[str], None] = lambda _m: None,
) -> MigrationResult:
    """Copy every catalog archive from the active backend to *dest_uri*.

    Verifies SHA-256 on read and (when *deep_verify*) on the written object,
    journals progress, and flips the persisted ``storage_uri`` to *dest_uri*
    only when nothing failed.  Idempotent under *resume*.
    """
    state_dir = Path(state_dir)
    default_uri = f"file://{state_dir}"
    source_uri = archive_store.load_storage_uri(state_dir, default_uri)

    if archive_store.archive_uri(source_uri, "x") == archive_store.archive_uri(dest_uri, "x"):
        raise MigrationError(
            f"destination {dest_uri!r} is the same as the current backend {source_uri!r}"
        )

    archives = iter_catalog_archives(state_dir, database_url)
    journal = MigrationJournal.load_or_new(state_dir, source_uri, dest_uri)
    result = MigrationResult(total=len(archives), source_uri=source_uri, dest_uri=dest_uri)
    log(f"migrating {len(archives)} archive(s): {source_uri} -> {dest_uri}")

    for ref in archives:
        if resume and journal.is_verified(ref.filename):
            result.skipped += 1
            continue
        try:
            if not archive_store.exists(source_uri, ref.filename):
                raise MigrationError("source archive missing")

            # Copy, hashing the streamed bytes (verifies the SOURCE read).
            copied_sha = archive_store.copy_archive(source_uri, dest_uri, ref.filename)
            if ref.sha256 and copied_sha != ref.sha256:
                raise MigrationError(
                    f"source integrity: catalog sha256 {ref.sha256} != read {copied_sha}"
                )

            # Re-read from the destination (verifies the WRITE landed intact).
            if deep_verify:
                dest_sha = archive_store.sha256_of(dest_uri, ref.filename)
                if ref.sha256 and dest_sha != ref.sha256:
                    raise MigrationError(
                        f"destination integrity: stored sha256 {dest_sha} != {ref.sha256}"
                    )

            journal.mark(ref.filename, VERIFIED, sha256=ref.sha256)
            result.migrated += 1
            log(f"  ok  {ref.filename}")
        except Exception as exc:  # noqa: BLE001 — record and continue
            journal.mark(ref.filename, FAILED, sha256=ref.sha256, error=str(exc))
            result.failures.append((ref.filename, str(exc)))
            log(f"  FAIL {ref.filename}: {exc}")
        finally:
            journal.save()

    if result.ok:
        # Every archive is present and verified at the destination — flip the
        # active backend so the server serves from it after restart.
        archive_store.save_storage_uri(state_dir, dest_uri)
        journal.completed = True
        journal.save()
        result.flipped = True
        log(f"migration complete — active storage_uri is now {dest_uri}")
    else:
        log(
            f"migration INCOMPLETE — {len(result.failures)} archive(s) failed; "
            f"storage_uri left at {source_uri}. Re-run to resume, or run "
            f"`storage doctor --heal`."
        )
    return result
