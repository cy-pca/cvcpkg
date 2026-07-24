# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Detect packages that would clobber each other's files.

Declared exclusivity (``conflicts:`` / ``provides:`` in a recipe) is what the
installer *enforces*: a conflict has to be caught before anything is
downloaded, and at that point all cvcpkg has is the catalog — you cannot
inspect the files of a package you have not fetched.

This module is the other half.  Bundle manifests already carry each package's
file list, so an overlap can be *computed* after the fact and compared against
what the recipes declare.  That turns a hand-maintained, drift-prone
declaration into something verifiable: an overlap with no declaration is a
clobber waiting to happen, and a declaration with no overlap is likely stale.

Computation cannot replace declaration — it comes too late to gate an install,
and it can only see packages that have been built.  It is a checker, not an
enforcement mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileOverlap:
    """Two packages that ship at least one identical path."""

    a: str
    b: str
    paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pair(self) -> tuple[str, str]:
        """The pair as an order-independent key."""
        return (self.a, self.b) if self.a <= self.b else (self.b, self.a)


def _normalize(path: str) -> str:
    """Compare paths the way a prefix would see them."""
    return path.replace("\\", "/").strip("/")


def find_file_overlaps(
    file_lists: dict[str, list[str]],
    *,
    ignore: frozenset[str] = frozenset(),
) -> list[FileOverlap]:
    """Return every pair of packages sharing an installed path.

    *file_lists* maps package name -> the paths it installs, relative to the
    prefix (as recorded in a bundle manifest's ``contents.files``).

    Directories that every package legitimately shares are not overlaps — only
    identical *file* paths are, so callers should pass file lists, not
    directory listings.  *ignore* drops known-shared paths (e.g. a registry
    file several packages append to).
    """
    owners: dict[str, set[str]] = {}
    for pkg, paths in file_lists.items():
        for raw in paths:
            p = _normalize(raw)
            if not p or p in ignore:
                continue
            owners.setdefault(p, set()).add(pkg)

    pairs: dict[tuple[str, str], set[str]] = {}
    for path, pkgs in owners.items():
        if len(pkgs) < 2:
            continue
        ordered = sorted(pkgs)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                pairs.setdefault((a, b), set()).add(path)

    return [
        FileOverlap(a=a, b=b, paths=tuple(sorted(paths))) for (a, b), paths in sorted(pairs.items())
    ]


def undeclared_conflicts(
    file_lists: dict[str, list[str]],
    declared: dict[str, list[str]],
    *,
    ignore: frozenset[str] = frozenset(),
) -> list[FileOverlap]:
    """Overlaps that no recipe declares — each is a latent clobber.

    *declared* is the ``{package: [conflicting, ...]}`` mapping from
    ``collect_recipe_conflicts`` (which already folds in ``provides`` slots).
    A pair counts as declared if *either* side names the other, so this does
    not double-report asymmetric declarations — use ``asymmetric_conflicts``
    for those.
    """
    out = []
    for ov in find_file_overlaps(file_lists, ignore=ignore):
        if ov.b in declared.get(ov.a, []) or ov.a in declared.get(ov.b, []):
            continue
        out.append(ov)
    return out


def asymmetric_conflicts(declared: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Return ``(declarer, missing)`` pairs where only one side declares.

    The installer resolves conflicts from the recipes of the packages *being
    installed*, so a one-sided declaration only fires in one direction: if A
    declares B but B does not declare A, installing B onto an existing A is
    not caught.  Only pairs where both packages appear in *declared* can be
    judged — a package absent from the mapping was simply not loaded.
    """
    out: list[tuple[str, str]] = []
    for a, conflicts in declared.items():
        for b in conflicts:
            if b in declared and a not in declared[b]:
                out.append((a, b))
    return sorted(out)
