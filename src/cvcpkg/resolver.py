# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Backtracking SAT-style resolver for component bundles.

See docs/roadmap/split-distribution.md §5.5 for the algorithm
description.  The resolver searches the catalog for a consistent
assignment of bundle versions that satisfies all user requirements
and inter-component dependency constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cvcpkg.errors import ResolveError
from cvcpkg.manifest import CatalogEntry, ComponentReq
from cvcpkg.semver import Version, satisfies, version_sort_key


@dataclass
class ResolveResult:
    """Successful resolution: a mapping of component name → chosen entry."""

    picked: dict[str, CatalogEntry] = field(default_factory=dict)


def resolve(
    requirements: list[ComponentReq],
    candidates: dict[str, list[CatalogEntry]],
    *,
    recommended: dict[str, str] | None = None,
    capabilities: set[str] | None = None,
) -> ResolveResult:
    """Resolve *requirements* against *candidates*.

    Parameters
    ----------
    requirements:
        Top-level component requests from the user.
    candidates:
        ``{component_name: [CatalogEntry, ...]}`` — all available
        bundles for each component, pre-filtered by platform tuple.
    recommended:
        Optional ``{component: version}`` map from a pinned release's
        ``recommended:`` block. Used to prefer the baseline version.
    capabilities:
        Host capabilities used to filter and rank providers of virtual
        packages.  Defaults to :func:`cvcpkg.platform.host_capabilities`
        when ``None`` (probed lazily, never at import time).

    Returns
    -------
    ResolveResult with ``picked`` mapping component names to chosen entries.

    A needed name ``N`` resolves against the union of (a) concrete entries
    literally named ``N`` and (b) any entry whose ``provides`` lists ``N``
    (a *virtual* package).  Candidates whose ``requires_capabilities`` are
    not all met by *capabilities* are dropped; among the survivors, those
    satisfying more required capabilities rank first (so a cuda provider
    beats a plain one on a cuda host), falling back to the usual
    version/recommended ordering.

    Raises
    ------
    ResolveError if no consistent assignment exists.
    """
    recommended = recommended or {}
    if capabilities is None:
        from cvcpkg.platform import host_capabilities

        capabilities = host_capabilities()

    # Build initial constraint set from user requirements.
    constraints: dict[str, str] = {}  # name → version spec
    excluded: set[str] = set()

    for req in requirements:
        if req.exclude:
            excluded.add(req.name)
            continue
        constraints[req.name] = req.version

    # Provider map: virtual name → entries whose ``provides`` lists it.
    providers: dict[str, list[CatalogEntry]] = {}
    for entries in candidates.values():
        for entry in entries:
            for virtual_name in entry.provides:
                providers.setdefault(virtual_name, []).append(entry)

    # Sort candidates for every resolvable name — concrete component names
    # plus virtual provider names — applying the capability filter and rank.
    sorted_candidates: dict[str, list[CatalogEntry]] = {}
    # Names whose whole candidate pool was filtered out by capabilities, mapped
    # to the capabilities the host would need, for a clear error message.
    capability_blocked: dict[str, set[str]] = {}
    for name in set(candidates) | set(providers):
        if name in excluded:
            continue
        pool = _candidate_pool(name, candidates, providers)
        selectable = [e for e in pool if set(e.requires_capabilities) <= capabilities]
        if pool and not selectable:
            missing: set[str] = set()
            for e in pool:
                missing |= set(e.requires_capabilities) - capabilities
            capability_blocked[name] = missing
        # Version/recommended order first, then a *stable* re-sort that floats
        # providers meeting more required capabilities to the front.
        ordered = _sort_candidates(selectable, recommended.get(name, ""))
        ordered.sort(key=lambda e: len(e.requires_capabilities), reverse=True)
        sorted_candidates[name] = ordered

    # Determine which components we need to resolve (user-requested + transitive).
    needed = {req.name for req in requirements if not req.exclude}

    picked: dict[str, CatalogEntry] = {}
    conflict_trail: list[str] = []

    if not _backtrack(
        needed, constraints, sorted_candidates, picked, conflict_trail, capability_blocked
    ):
        raise ResolveError("cannot satisfy requirements:\n  " + "\n  ".join(conflict_trail[-10:]))

    return ResolveResult(picked=picked)


def _candidate_pool(
    name: str,
    candidates: dict[str, list[CatalogEntry]],
    providers: dict[str, list[CatalogEntry]],
) -> list[CatalogEntry]:
    """Union of concrete entries named *name* and providers of *name*.

    De-duplicated by object identity (``CatalogEntry`` is unhashable) while
    preserving order: concrete entries first, then providers.
    """
    pool: list[CatalogEntry] = []
    seen: set[int] = set()
    for entry in candidates.get(name, []) + providers.get(name, []):
        if id(entry) not in seen:
            seen.add(id(entry))
            pool.append(entry)
    return pool


def _sort_candidates(entries: list[CatalogEntry], recommended_ver: str) -> list[CatalogEntry]:
    """Sort candidates: recommended first, then highest version.

    Everything is ordered by the canonical ``version_sort_key`` (descending),
    so it agrees with the resolver's ``satisfies()`` filtering and the +cvc.N
    tiebreak.  Entries whose version is not semver (e.g. openssh's
    "10.4p1+cvc.1") cannot be dropped — that made published bundles silently
    uninstallable ("no candidate for 'openssh'") — so the key ranks them below
    every parseable candidate yet keeps them *ordered* among themselves (by
    natural key + cvc revision) instead of dumping them last lexically.
    Version-range constraints still reject them in _backtrack /
    _compatible_with_picked (satisfies() raising counts as no-match).
    """
    ordered = sorted(
        entries,
        key=lambda e: version_sort_key(e.version, e.cvc_revision),
        reverse=True,
    )

    if not recommended_ver:
        return ordered

    # Prefer an exact match on cvc_revision when the recommendation carries
    # one; otherwise fall back to base-version equality.  An unparseable
    # recommendation (rv is None) matches nothing — every entry flows to rest.
    try:
        rv: Version | None = Version.parse(recommended_ver)
    except ValueError:
        rv = None
    rv_rev = rv.cvc_revision if rv is not None else 0

    recommended_entry: CatalogEntry | None = None
    rest: list[CatalogEntry] = []
    for e in ordered:
        is_match = False
        if rv is not None and recommended_entry is None:
            try:
                v = Version.parse(e.version)
            except ValueError:
                v = None
            if v is not None and v == rv and (rv_rev == 0 or v.cvc_revision == rv_rev):
                is_match = True
        if is_match:
            recommended_entry = e
        else:
            rest.append(e)

    if recommended_entry is not None:
        return [recommended_entry] + rest
    return rest


def _backtrack(
    needed: set[str],
    constraints: dict[str, str],
    candidates: dict[str, list[CatalogEntry]],
    picked: dict[str, CatalogEntry],
    trail: list[str],
    capability_blocked: dict[str, set[str]] | None = None,
) -> bool:
    """Recursive backtracking resolver."""
    capability_blocked = capability_blocked or {}
    # Find the next unpicked component.
    unpicked = needed - picked.keys()
    if not unpicked:
        return True

    name = min(unpicked)  # deterministic order
    available = candidates.get(name, [])
    spec = constraints.get(name, "")

    for entry in available:
        # Check user-level version constraint.
        if spec:
            try:
                if not satisfies(entry.version, spec):
                    continue
            except ValueError:
                continue

        # Check compatibility with already-picked bundles' constraints on us.
        if not _compatible_with_picked(name, entry, picked):
            trail.append(f"{name}=={entry.version} conflicts with already-picked dependencies")
            continue

        # Tentatively pick this candidate.
        picked[name] = entry

        # Add transitive dependencies to the needed set.
        new_needed = set(needed)
        new_constraints = dict(constraints)
        for dep in entry.required_deps:
            new_needed.add(dep.name)
            if dep.version:
                existing = new_constraints.get(dep.name, "")
                if existing and existing != dep.version:
                    # Two different constraints on the same dep — take the intersection
                    # by combining them with a comma.
                    new_constraints[dep.name] = f"{existing},{dep.version}"
                else:
                    new_constraints[dep.name] = dep.version

        if _backtrack(new_needed, new_constraints, candidates, picked, trail, capability_blocked):
            return True

        # Backtrack.
        del picked[name]

    if not available and name in capability_blocked:
        missing = ", ".join(sorted(capability_blocked[name]))
        trail.append(
            f"no provider for '{name}' meets required capabilities (host is missing: {missing})"
        )
    else:
        trail.append(f"no candidate for '{name}' satisfies constraints (spec={spec!r})")
    return False


def _compatible_with_picked(
    name: str,
    candidate: CatalogEntry,
    picked: dict[str, CatalogEntry],
) -> bool:
    """Check that *candidate* doesn't violate constraints from already-picked bundles."""
    for _picked_name, picked_entry in picked.items():
        for dep in picked_entry.required_deps:
            if dep.name == name and dep.version:
                try:
                    if not satisfies(candidate.version, dep.version):
                        return False
                except ValueError:
                    return False
    return True
