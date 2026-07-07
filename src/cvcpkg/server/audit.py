"""Append-only audit log for cvcpkg-server.

Every mutation (publish, yank, delete, token create/revoke) is
recorded as a YAML entry with a chained SHA-256 hash of the
previous entry, forming a tamper-evident chain.

The log file is ``<state_dir>/audit.yaml``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from threading import Lock

import yaml

from cvcpkg.server.models import AuditAction, AuditEntry

_AUDIT_FILE = "audit.yaml"


class AuditLog:
    """Append-only audit trail with chained integrity hashes."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._path = state_dir / _AUDIT_FILE
        self._lock = Lock()
        self._entries: list[AuditEntry] = []
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        with open(self._path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            return
        self._entries = [AuditEntry(**e) for e in data]

    def _entry_hash(self, entry: AuditEntry) -> str:
        """Compute the SHA-256 of an entry's content for chaining."""
        payload = json.dumps(
            {
                "id": entry.id,
                "timestamp": entry.timestamp.isoformat(),
                "action": entry.action.value,
                "actor": entry.actor,
                "target": entry.target,
                "detail": entry.detail,
                "prev_sha256": entry.prev_sha256,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def record(
        self,
        action: AuditAction,
        actor: str,
        target: str,
        detail: str = "",
    ) -> AuditEntry:
        """Append an entry to the audit log and flush to disk."""
        with self._lock:
            prev_hash = ""
            if self._entries:
                prev_hash = self._entry_hash(self._entries[-1])

            entry = AuditEntry(
                id=len(self._entries) + 1,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
                action=action,
                actor=actor,
                target=target,
                detail=detail,
                prev_sha256=prev_hash,
            )
            self._entries.append(entry)
            self._flush()
            return entry

    def entries(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        action: AuditAction | None = None,
        target: str = "",
    ) -> tuple[list[AuditEntry], int]:
        """Return filtered audit entries and total count."""
        filtered = self._entries
        if action is not None:
            filtered = [e for e in filtered if e.action == action]
        if target:
            filtered = [e for e in filtered if e.target == target]
        total = len(filtered)
        page = filtered[offset : offset + limit]
        return page, total

    def verify_chain(self) -> tuple[bool, str]:
        """Verify the integrity of the entire audit chain.

        Returns (ok, message).
        """
        if not self._entries:
            return True, "empty log"

        # First entry should have empty prev_sha256
        if self._entries[0].prev_sha256:
            return False, "first entry has non-empty prev_sha256"

        for i in range(1, len(self._entries)):
            expected = self._entry_hash(self._entries[i - 1])
            if self._entries[i].prev_sha256 != expected:
                return (
                    False,
                    f"chain broken at entry {self._entries[i].id}: "
                    f"expected {expected}, got {self._entries[i].prev_sha256}",
                )
        return True, f"chain intact ({len(self._entries)} entries)"

    def _flush(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            yaml.safe_dump(
                [e.model_dump(mode="json") for e in self._entries],
                f,
                default_flow_style=False,
                sort_keys=False,
            )
