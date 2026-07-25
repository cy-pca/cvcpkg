# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Selective-mirroring policy for edge/satellite populate (roadmap Phase 12).

An edge/satellite server (``CVCPKG_POPULATE_UPSTREAM`` set) mirrors the public
catalog from an upstream root.  Operators frequently do **not** want to mirror
everything — some packages are very large (e.g. ``qt6``, ``vtk``, CUDA libs) —
so this module decides, per upstream bundle, whether to mirror it.

Config (env, comma-separated where applicable):

- ``CVCPKG_POPULATE_INCLUDE`` — package-name **allowlist**.  If set, *only*
  these packages are mirrored.
- ``CVCPKG_POPULATE_EXCLUDE`` — package-name **denylist**.  These packages are
  never mirrored (evaluated before the allowlist).
- ``CVCPKG_POPULATE_PLATFORMS`` — platform allowlist (existing knob).
- ``CVCPKG_POPULATE_MAX_PACKAGE_BYTES`` — per-package size cap; defaults to
  ``CVCPKG_MAX_UPLOAD_BYTES``.  A bundle larger than this is skipped.

The policy is a pure value object so the decision logic is unit-testable in
isolation from the populate loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


@dataclass(frozen=True)
class MirrorDecision:
    """Result of evaluating a bundle against the mirror policy."""

    mirror: bool
    reason: str = ""


@dataclass(frozen=True)
class EvictionCandidate:
    """A mirrored item eligible for usage-based eviction."""

    key: object  # opaque identifier (the caller maps it back to a package)
    size_bytes: int
    downloads: int


def select_evictions(
    candidates: list[EvictionCandidate], budget_bytes: int
) -> list[EvictionCandidate]:
    """Pick the least-valuable mirrored items to evict to fit *budget_bytes*.

    Keeps the most-downloaded items within budget: evicts **least-downloaded
    first**, breaking ties by **largest size first** (frees more per
    eviction).  Returns the items to evict, in eviction order.  A
    non-positive budget means "unbounded" — nothing is evicted.
    """
    total = sum(c.size_bytes for c in candidates)
    if budget_bytes <= 0 or total <= budget_bytes:
        return []
    order = sorted(candidates, key=lambda c: (c.downloads, -c.size_bytes))
    evict: list[EvictionCandidate] = []
    for c in order:
        if total <= budget_bytes:
            break
        evict.append(c)
        total -= c.size_bytes
    return evict


@dataclass(frozen=True)
class MirrorPolicy:
    """Which upstream packages an edge/satellite mirrors."""

    include: frozenset[str] = field(default_factory=frozenset)
    exclude: frozenset[str] = field(default_factory=frozenset)
    platforms: frozenset[str] = field(default_factory=frozenset)
    max_package_bytes: int = 0  # 0 == no per-package size cap

    @classmethod
    def from_env(cls, default_max_bytes: int = 0) -> MirrorPolicy:
        """Build a policy from the ``CVCPKG_POPULATE_*`` environment."""
        raw_max = os.environ.get("CVCPKG_POPULATE_MAX_PACKAGE_BYTES", "").strip()
        max_bytes = int(raw_max) if raw_max else default_max_bytes
        return cls(
            include=frozenset(_csv_set(os.environ.get("CVCPKG_POPULATE_INCLUDE", ""))),
            exclude=frozenset(_csv_set(os.environ.get("CVCPKG_POPULATE_EXCLUDE", ""))),
            platforms=frozenset(_csv_set(os.environ.get("CVCPKG_POPULATE_PLATFORMS", ""))),
            max_package_bytes=max_bytes,
        )

    def decide(self, *, name: str, platform: str = "", size_bytes: int = 0) -> MirrorDecision:
        """Decide whether to mirror one upstream bundle.

        Order: denylist → allowlist → platform allowlist → size cap.  The
        denylist wins over the allowlist so a broad allowlist can still carve
        out specific packages.
        """
        if self.exclude and name in self.exclude:
            return MirrorDecision(False, "excluded by CVCPKG_POPULATE_EXCLUDE")
        if self.include and name not in self.include:
            return MirrorDecision(False, "not in CVCPKG_POPULATE_INCLUDE allowlist")
        # A noarch package (platform "any") is platform-independent: it is used
        # by *every* concrete platform, including the ones an operator did
        # allowlist, so a platform allowlist must never filter it out. Excluding
        # it silently starves the entire pure-Python (py3-none-any) closure on a
        # cluster that mirrors, say, only linux/windows — exactly the failure
        # that left the dev cluster with 0 of typing-extensions/click/... .
        if self.platforms and platform and platform != "any" and platform not in self.platforms:
            return MirrorDecision(False, "platform not in CVCPKG_POPULATE_PLATFORMS")
        if self.max_package_bytes and size_bytes > self.max_package_bytes:
            return MirrorDecision(
                False,
                f"{size_bytes} bytes exceeds mirror per-package cap " f"({self.max_package_bytes})",
            )
        return MirrorDecision(True)

    def is_active(self) -> bool:
        """True if any selective knob is set (else everything is mirrored)."""
        return bool(self.include or self.exclude or self.platforms or self.max_package_bytes)
