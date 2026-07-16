"""Host-tools install record + strip logic.

When cvcpkg builds for a target that needs cross-compilation, the build-time
host tools (cmake, ninja, bazel/bazelisk, cross-toolchains, ...) install into a
SEPARATE *host-tools prefix* so the deliverable ``--prefix`` contains only
target artifacts (see ``--host-tools-prefix`` on ``cvcpkg build``).

Those host tools are a build-time byproduct: they are not part of the
deliverable and should not ship with it.  This module records the separation
in the deliverable prefix (``share/libcvc-deps/host-tools.yaml``) so the
install/finalize step can find and strip the host-tools prefix -- unless the
caller explicitly keeps it (``--keep-host-tools``).

The record is the source of truth for the strip: install reads it rather than
guessing a sibling directory by naming convention.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Location of the host-tools record within a deliverable prefix.
_RECORD_REL = Path("share") / "libcvc-deps" / "host-tools.yaml"

_SCHEMA_VERSION = 1


@dataclass
class HostToolsRecord:
    """Parsed ``share/libcvc-deps/host-tools.yaml``.

    ``present`` marks that a host-tools prefix was created alongside this
    deliverable; ``prefix`` is where it lives; ``tools`` are the host-tool
    package names that were installed into it; ``stripped`` records whether
    the strip has already run.
    """

    schema_version: int = _SCHEMA_VERSION
    present: bool = False
    prefix: str = ""
    tools: list[str] = field(default_factory=list)
    stripped: bool = False
    stripped_at: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "host_tools": {
                "present": self.present,
                "prefix": self.prefix,
                "tools": list(self.tools),
                "stripped": self.stripped,
                "stripped_at": self.stripped_at,
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> HostToolsRecord:
        ht = d.get("host_tools", {}) if isinstance(d, dict) else {}
        if not isinstance(ht, dict):
            ht = {}
        return cls(
            schema_version=d.get("schema_version", _SCHEMA_VERSION),
            present=bool(ht.get("present", False)),
            prefix=ht.get("prefix", "") or "",
            tools=list(ht.get("tools", []) or []),
            stripped=bool(ht.get("stripped", False)),
            stripped_at=ht.get("stripped_at", "") or "",
        )


def record_path(deliverable_prefix: Path) -> Path:
    """Path of the host-tools record inside *deliverable_prefix*."""
    return deliverable_prefix / _RECORD_REL


def write_host_tools_record(
    deliverable_prefix: Path,
    host_tools_prefix: Path,
    tools: list[str],
    *,
    stripped: bool = False,
    stripped_at: str = "",
) -> Path:
    """Write/refresh the host-tools record into *deliverable_prefix*.

    Returns the path of the written record.
    """
    rec = HostToolsRecord(
        present=True,
        prefix=str(host_tools_prefix),
        tools=sorted(set(tools)),
        stripped=stripped,
        stripped_at=stripped_at,
    )
    path = record_path(deliverable_prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(rec.to_dict(), f, default_flow_style=False, sort_keys=False)
    return path


def read_host_tools_record(deliverable_prefix: Path) -> HostToolsRecord | None:
    """Read the host-tools record from *deliverable_prefix*, or None if absent."""
    path = record_path(deliverable_prefix)
    if not path.is_file():
        return None
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return None
    return HostToolsRecord.from_dict(data)


def strip_host_tools(
    deliverable_prefix: Path,
    *,
    keep: bool = False,
    now: str | None = None,
) -> Path | None:
    """Strip the host-tools prefix recorded for *deliverable_prefix*.

    Reads the record; if host tools are present and *keep* is False, deletes
    the recorded host-tools prefix directory and rewrites the record with
    ``stripped=true`` (kept for provenance).  Returns the stripped path, or
    ``None`` when there is nothing to strip: no record, ``keep=True``, already
    stripped, an empty prefix field, or a prefix that resolves to the
    deliverable itself (separation disabled -- never delete the deliverable).

    The strip is verified: ``stripped=true`` is recorded only when the prefix
    is actually gone afterwards.  A partial removal (locked/read-only files) or
    a swallowed error leaves the record ``stripped=false`` -- i.e. retryable --
    and returns ``None`` rather than falsely reporting success.
    """
    rec = read_host_tools_record(deliverable_prefix)
    if rec is None or not rec.present:
        return None
    if keep or rec.stripped or not rec.prefix:
        return None

    target = Path(rec.prefix)
    try:
        same = target.resolve() == deliverable_prefix.resolve()
    except OSError:
        same = str(target) == str(deliverable_prefix)
    if same:
        # Separation was disabled (host-tools prefix == deliverable prefix);
        # stripping would delete the deliverable.  Refuse.
        return None

    stamp = now or datetime.now(timezone.utc).isoformat()
    # Remove the host-tools prefix.  rmtree refuses symlinks, so unlink those
    # explicitly (drop only the link, never its target -- we do not own it);
    # otherwise best-effort recursive delete.
    if target.is_symlink():
        target.unlink(missing_ok=True)
    elif target.exists():
        shutil.rmtree(target, ignore_errors=True)

    # Record stripped=true only if the prefix is genuinely gone; a leftover
    # (partial delete, locked files) stays present+unstripped so the next
    # build/install retries instead of short-circuiting on a false success.
    gone = not target.exists() and not target.is_symlink()
    write_host_tools_record(
        deliverable_prefix,
        target,
        rec.tools,
        stripped=gone,
        stripped_at=stamp if gone else "",
    )
    return target if gone else None
