# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The jobs a builder holds, on disk, so the next incarnation can report them.

A builder claims a job, builds it and reports the outcome; the report is the
only thing that ends the job on the server.  A builder process that dies
mid-job never reports, and when its supervisor restarts it within seconds the
server never sees it go offline either: the new process registers the same
builder row and heartbeats, so the offline reaper leaves the job alone, and
the job sits ``running`` until the build timeout reaps it (2 h by default),
with every job of its DAG that depends on it waiting the whole time.

That is what happened to dev job 25560 (grpc, netbsd) on 2026-11-27: the
worker died of SIGPIPE mid-build, the fleet supervisor restarted it five
seconds later, and the new worker swept the job's directories ("reclaimed 2
orphaned job dir(s)") without a word to the server.  The job timed out two
hours later and its dependents (libcvc, ari, arisrv, ...) were cancelled.

So each claim is recorded here, one small file per job, written when the
claim lands and removed once the job's thread is done with it.  A record
still present when a builder starts names a job a previous process of the
same builder claimed and never finished; the builder reports each such job as
failed (see ``builder_run``), which cascades to its dependents at once.

Records live in ``<work dir>/.cvcpkg-claims/`` -- a name no scratch sweep
matches (``builder_gc`` and ``scripts/reap-stale-build-dirs.sh`` take only
``cvcpkg-*``).  Each is keyed by the server and builder name as well as the
job id, so builders that share a directory (no ``--work-dir``) never report
each other's jobs.  Everything here is best-effort: a record that cannot be
written costs only the report it would have enabled, never the job.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

#: Directory, inside the builder's work dir, that holds the records.
CLAIMS_DIR_NAME = ".cvcpkg-claims"


def _owner_key(server: str, builder: str) -> str:
    """A short, filesystem-safe key for one builder of one server."""
    digest = hashlib.sha256(f"{server.rstrip('/')}\n{builder}".encode()).hexdigest()
    return digest[:12]


class ClaimLedger:
    """The claims one builder (*builder* on *server*) holds, under *root*.

    Owned by the ``builder run`` invocation that creates it; holds no state of
    its own beyond the directory it writes to.
    """

    def __init__(self, root: Path | str, *, server: str, builder: str) -> None:
        self.dir = Path(root) / CLAIMS_DIR_NAME
        self.server = server.rstrip("/")
        self.builder = builder
        self._key = _owner_key(self.server, builder)

    def _path(self, job_id: int) -> Path:
        return self.dir / f"{int(job_id)}-{self._key}.json"

    def record(self, job_id: int, *, identity: dict[str, Any], recipe: str = "") -> bool:
        """Note that this builder now holds *job_id*; False if it could not.

        *identity* is what the claim named the builder by (``builder_id`` or
        ``claimant``), so the eventual report can name it the same way even if
        the builder has been re-registered under another id since.
        """
        body = {
            "job_id": int(job_id),
            "server": self.server,
            "builder": self.builder,
            "identity": dict(identity),
            "recipe": recipe,
            "pid": os.getpid(),
            "claimed_at": time.time(),
        }
        path = self._path(job_id)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(body), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def forget(self, job_id: int) -> None:
        """Drop the record for *job_id*, if there is one."""
        try:
            self._path(job_id).unlink(missing_ok=True)
        except OSError:
            pass

    def stranded(self) -> list[dict[str, Any]]:
        """The records a previous process of this builder left, oldest job first.

        Call it before this process claims anything.  A record that cannot be
        parsed names no job anyone could report, so it is removed.
        """
        found: list[dict[str, Any]] = []
        try:
            # Half-written records of a process that died inside record().
            for tmp in self.dir.glob(f"*-{self._key}.json.*.tmp"):
                tmp.unlink(missing_ok=True)
            paths = sorted(self.dir.glob(f"*-{self._key}.json"))
        except OSError:
            return found
        for path in paths:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                job_id = int(rec["job_id"])
                if (
                    str(rec.get("server", "")).rstrip("/") != self.server
                    or rec.get("builder") != self.builder
                ):
                    continue  # a key collision: not ours to report
                identity = rec.get("identity")
                rec["job_id"] = job_id
                rec["identity"] = identity if isinstance(identity, dict) else {}
            except OSError:
                continue
            except (ValueError, TypeError, KeyError, AttributeError):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            found.append(rec)
        found.sort(key=lambda r: r["job_id"])
        return found
