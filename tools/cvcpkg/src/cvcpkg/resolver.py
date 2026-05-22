"""Backtracking SAT-style resolver for component bundles.

See docs/roadmap/split-distribution.md §5.5 for the algorithm
description.  The resolver searches the catalog for a consistent
assignment of bundle versions that satisfies all user requirements
and inter-component dependency constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cvcpkg.errors import ResolveError
from cvcpkg.manifest import CatalogEntry, ComponentReq, Dependency
from cvcpkg.semver import Version, satisfies


@dataclass
class ResolveResult:
    """Successful resolution: a mapping of component name → chosen entry."""

    picked: dict[str, CatalogEntry] = field(default_factory=dict)


def resolve(
    requirements: list[ComponentReq],
    candidates: dict[str, list[CatalogEntry]],
    *,
    recommended: dict[str, str] | None = None,
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

    Returns
    -------
    ResolveResult with ``picked`` mapping component names to chosen entries.

    Raises
    ------
    ResolveError if no consistent assignment exists.
    """
    recommended = recommended or {}

    # Build initial constraint set from user requirements.
    constraints: dict[str, str] = {}  # name → version spec
    excluded: set[str] = set()

    for req in requirements:
        if req.exclude:
            excluded.add(req.name)
            continue
        constraints[req.name] = req.version

    # Sort candidates for each component by preference.
    sorted_candidates: dict[str, list[CatalogEntry]] = {}
    for name, entries in candidates.items():
        if name in excluded:
            continue
        sorted_candidates[name] = _sort_candidates(entries, recommended.get(name, ""))

    # Determine which components we need to resolve (user-requested + transitive).
    needed = {req.name for req in requirements if not req.exclude}

    picked: dict[str, CatalogEntry] = {}
    conflict_trail: list[str] = []

    if not _backtrack(needed, constraints, sorted_candidates, picked, conflict_trail):
        raise ResolveError(
            "cannot satisfy requirements:\n  " + "\n  ".join(conflict_trail[-10:])
        )

    return ResolveResult(picked=picked)


def _sort_candidates(entries: list[CatalogEntry], recommended_ver: str) -> list[CatalogEntry]:
    """Sort candidates: recommended first, then highest version.

    We return a list where recommended (if present) is first, then remaining
    candidates sorted by descending version.
    """
    recommended_entry: CatalogEntry | None = None
    rest: list[CatalogEntry] = []

    if recommended_ver:
        try:
            rv = Version.parse(recommended_ver)
        except ValueError:
            rv = None
        for e in entries:
            v = Version.parse(e.version)
            if rv is not None and v == rv and recommended_entry is None:
                recommended_entry = e
            else:
                rest.append(e)
    else:
        rest = list(entries)

    rest.sort(key=lambda e: Version.parse(e.version), reverse=True)

    if recommended_entry is not None:
        return [recommended_entry] + rest
    return rest


def _backtrack(
    needed: set[str],
    constraints: dict[str, str],
    candidates: dict[str, list[CatalogEntry]],
    picked: dict[str, CatalogEntry],
    trail: list[str],
) -> bool:
    """Recursive backtracking resolver."""
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
            trail.append(
                f"{name}=={entry.version} conflicts with already-picked dependencies"
            )
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

        if _backtrack(new_needed, new_constraints, candidates, picked, trail):
            return True

        # Backtrack.
        del picked[name]

    trail.append(f"no candidate for '{name}' satisfies constraints (spec={spec!r})")
    return False


def _compatible_with_picked(
    name: str,
    candidate: CatalogEntry,
    picked: dict[str, CatalogEntry],
) -> bool:
    """Check that *candidate* doesn't violate constraints from already-picked bundles."""
    for picked_name, picked_entry in picked.items():
        for dep in picked_entry.required_deps:
            if dep.name == name and dep.version:
                try:
                    if not satisfies(candidate.version, dep.version):
                        return False
                except ValueError:
                    return False
    return True
