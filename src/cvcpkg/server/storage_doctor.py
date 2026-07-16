"""Diagnose and heal a cvcpkg server's archive store.

``cvcpkg-server storage doctor`` checks that every catalog-referenced archive
is present in the server's ACTIVE storage backend with the SHA-256 the catalog
recorded, and surfaces the fallout of a botched migration:

  * **missing**   — a catalog archive absent from the active backend (e.g. a
    migration that copied only part of the set before dying);
  * **corrupt**   — an archive whose bytes no longer hash to the catalog's
    SHA-256 (a truncated/partial upload, bit-rot, a clobbered object);
  * **size-mismatch** — a shallow-scan smell when the recorded size differs;
  * **incomplete migration** — a ``migration-journal.yaml`` that never reached
    ``completed`` (pending/failed entries).

With ``--heal`` it repairs what it can: missing/corrupt archives are re-copied
from a ``--source`` backend (typically the pre-migration one) with integrity
verified end-to-end, and an incomplete migration journal is resumed to its
recorded destination.  Anything it cannot safely fix (no source, or the source
copy is also missing/corrupt) is reported as *unhealable* rather than guessed
at.

Runs offline against the state dir, like ``storage migrate``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cvcpkg.server import archive_store
from cvcpkg.server.storage_migration import (
    MigrationJournal,
    MigrationResult,
    iter_catalog_archives,
    run_migration,
)

# Finding statuses.
MISSING = "missing"
CORRUPT = "corrupt"
SIZE_MISMATCH = "size-mismatch"


def _noop(_msg: str) -> None:
    """Default no-op progress logger."""


@dataclass
class Finding:
    filename: str
    status: str
    detail: str = ""


@dataclass
class DoctorReport:
    active_uri: str
    total: int
    findings: list[Finding] = field(default_factory=list)
    journal_incomplete: bool = False
    journal_pending: list[str] = field(default_factory=list)
    journal_dest: str = ""
    orphans: list[str] = field(default_factory=list)
    deep: bool = False

    @property
    def missing(self) -> list[Finding]:
        return [f for f in self.findings if f.status == MISSING]

    @property
    def corrupt(self) -> list[Finding]:
        return [f for f in self.findings if f.status in (CORRUPT, SIZE_MISMATCH)]

    @property
    def healthy(self) -> bool:
        return not self.findings and not self.journal_incomplete


@dataclass
class HealResult:
    healed: list[str] = field(default_factory=list)
    unhealable: list[tuple[str, str]] = field(default_factory=list)
    resumed_migration: MigrationResult | None = None

    @property
    def ok(self) -> bool:
        migration_ok = self.resumed_migration is None or self.resumed_migration.ok
        return not self.unhealable and migration_ok


# ── Diagnose ────────────────────────────────────────────────────


def _find_orphans(active_uri: str, known: set[str]) -> list[str]:
    """Best-effort list of objects in the backend not referenced by the catalog."""
    root = archive_store.local_root(active_uri)
    if root is not None:
        try:
            return sorted(
                p.name
                for p in root.iterdir()
                if p.is_file() and p.name not in known and not p.name.endswith(".upload")
            )
        except OSError:
            return []
    # Remote backend: rely on the optional list() interface.
    try:
        from cvcpkg.storage import get_backend

        backend = get_backend(active_uri)
        prefix = archive_store.archive_uri(active_uri, "")
        return sorted(name for name in backend.list(prefix) if name and name not in known)
    except Exception:
        return []


def diagnose(
    state_dir: Path,
    *,
    database_url: str = "",
    deep: bool = False,
    check_orphans: bool = False,
) -> DoctorReport:
    """Inspect the active backend against the catalog.

    *deep* re-hashes every archive (authoritative but O(bytes)); the shallow
    default checks presence and, when the catalog knows it, size.
    """
    state_dir = Path(state_dir)
    active_uri = archive_store.load_storage_uri(state_dir, f"file://{state_dir}")
    archives = iter_catalog_archives(state_dir, database_url)
    findings: list[Finding] = []
    known: set[str] = set()

    for ref in archives:
        known.add(ref.filename)
        if not archive_store.exists(active_uri, ref.filename):
            findings.append(Finding(ref.filename, MISSING, "absent from active backend"))
            continue
        if deep and ref.sha256:
            try:
                actual = archive_store.sha256_of(active_uri, ref.filename)
            except Exception as exc:  # noqa: BLE001
                findings.append(Finding(ref.filename, MISSING, f"unreadable: {exc}"))
                continue
            if actual != ref.sha256:
                findings.append(
                    Finding(ref.filename, CORRUPT, f"sha256 {actual} != catalog {ref.sha256}")
                )
        elif ref.size >= 0:
            sz = archive_store.size(active_uri, ref.filename)
            if sz >= 0 and sz != ref.size:
                findings.append(
                    Finding(ref.filename, SIZE_MISMATCH, f"size {sz} != catalog {ref.size}")
                )

    journal = MigrationJournal.load(state_dir)
    incomplete = bool(journal and not journal.completed)
    pending = journal.pending_or_failed() if incomplete and journal else []
    dest = journal.dest_uri if incomplete and journal else ""

    orphans = _find_orphans(active_uri, known) if check_orphans else []

    return DoctorReport(
        active_uri=active_uri,
        total=len(archives),
        findings=findings,
        journal_incomplete=incomplete,
        journal_pending=pending,
        journal_dest=dest,
        orphans=orphans,
        deep=deep,
    )


# ── Heal ────────────────────────────────────────────────────────


def heal(
    state_dir: Path,
    *,
    database_url: str = "",
    source_uri: str = "",
    log: Callable[[str], None] = _noop,
) -> HealResult:
    """Repair missing/corrupt archives from *source_uri* and resume any
    incomplete migration.  Integrity is verified end-to-end; anything that
    cannot be safely restored is returned as *unhealable*."""
    state_dir = Path(state_dir)
    active_uri = archive_store.load_storage_uri(state_dir, f"file://{state_dir}")
    # Heal must know real corruption, not just absence → always deep.
    report = diagnose(state_dir, database_url=database_url, deep=True)
    refs = {r.filename: r for r in iter_catalog_archives(state_dir, database_url)}

    result = HealResult()
    for finding in report.missing + report.corrupt:
        fn = finding.filename
        want = refs.get(fn).sha256 if refs.get(fn) else ""
        if not source_uri:
            result.unhealable.append((fn, "no --source backend to restore from"))
            continue
        if not archive_store.exists(source_uri, fn):
            result.unhealable.append((fn, "source backend also missing this archive"))
            continue
        try:
            src_sha = archive_store.sha256_of(source_uri, fn)
        except Exception as exc:  # noqa: BLE001
            result.unhealable.append((fn, f"source unreadable: {exc}"))
            continue
        if want and src_sha != want:
            result.unhealable.append((fn, f"source copy also corrupt (sha256 {src_sha} != {want})"))
            continue
        copied = archive_store.copy_archive(source_uri, active_uri, fn)
        if want and copied != want:
            result.unhealable.append((fn, f"restore failed integrity ({copied} != {want})"))
            continue
        result.healed.append(fn)
        log(f"  restored {fn}")

    if report.journal_incomplete and report.journal_dest:
        log(f"resuming incomplete migration -> {report.journal_dest}")
        result.resumed_migration = run_migration(
            state_dir,
            report.journal_dest,
            database_url=database_url,
            resume=True,
            log=log,
        )

    return result
