# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""How ``/v1/search`` orders what a query matched: best match first.

A query is a substring matched across a dozen fields (name, description, tags,
platform, ...), so ``q=vtk`` also matches every package whose description
mentions VTK.  Ordered by name alone, the ``vtk`` package itself came last.
Each matching build gets a tier, lowest best, and a package ranks at the best
tier any of its matching builds reached:

* ``RANK_EXACT``: the name is the query (case-insensitive);
* ``RANK_PREFIX``: the name starts with it;
* ``RANK_NAME``: the name contains it;
* ``RANK_TAG``: a tag contains it;
* ``RANK_OTHER``: only another field matched (description, maintainer, ...).

Ties go by name, case-insensitively, then org (base packages first).  The DB
backend computes the same tiers in SQL (``DbPackageIndex``); the file-index
backend calls :func:`relevance_rank`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from cvcpkg.semver import version_sort_key

RANK_EXACT = 0
RANK_PREFIX = 1
RANK_NAME = 2
RANK_TAG = 3
RANK_OTHER = 4


def normalize_query(q: str) -> str:
    """The query as ranked: trimmed and lower-cased."""
    return (q or "").strip().lower()


def relevance_rank(name: str, tags: str, q: str) -> int:
    """The tier of one build matched by query *q* (see the module docstring)."""
    q = normalize_query(q)
    if not q:
        return RANK_EXACT
    name = (name or "").lower()
    if name == q:
        return RANK_EXACT
    if name.startswith(q):
        return RANK_PREFIX
    if q in name:
        return RANK_NAME
    if q in (tags or "").lower():
        return RANK_TAG
    return RANK_OTHER


def group_sort_key(rank: int, name: str, org: str) -> tuple[int, str, str, str]:
    """Order of packages in a result: tier, then name, then org."""
    return (rank, (name or "").lower(), name or "", org or "")


def summarize_group(
    name: str,
    org: str,
    builds: Iterable[Mapping[str, Any]],
    *,
    build_count: int | None = None,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    """One package's search summary from its matching builds.

    *builds* are mappings with at least ``version``, ``platform`` and ``arch``
    (plus ``size_bytes`` unless *size_bytes* is given).  ``version`` is the
    newest matching version.
    """
    versions: set[str] = set()
    platforms: set[str] = set()
    archs: set[str] = set()
    count = 0
    size = 0
    for b in builds:
        count += 1
        size += int(b.get("size_bytes") or 0)
        if b.get("version"):
            versions.add(str(b["version"]))
        if b.get("platform"):
            platforms.add(str(b["platform"]))
        if b.get("arch"):
            archs.add(str(b["arch"]))
    newest = max(versions, key=version_sort_key) if versions else ""
    return {
        "name": name,
        "org": org or "",
        "version": newest,
        "platforms": sorted(platforms),
        "archs": sorted(archs),
        "builds": count if build_count is None else build_count,
        "size_bytes": size if size_bytes is None else size_bytes,
    }
