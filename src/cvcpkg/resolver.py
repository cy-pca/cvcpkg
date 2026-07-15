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
        raise ResolveError("cannot satisfy requirements:\n  " + "\n  ".join(conflict_trail[-10:]))

    return ResolveResult(picked=picked)


def _sort_candidates(entries: list[CatalogEntry], recommended_ver: str) -> list[CatalogEntry]:
    """Sort candidates: recommended first, then highest version.

    We return a list where recommended (if present) is first, then remaining
    candidates sorted by descending version.
    """
    recommended_entry: CatalogEntry | None = None
    rest: list[CatalogEntry] = []

    # Parse once.  Entries whose version is not semver (e.g. openssh's
    # "10.4p1+cvc.1") cannot be ordered against the rest — but dropping
    # them made published bundles silently uninstallable ("no candidate
    # for 'openssh'").  Keep them: they are offered AFTER all parseable
    # candidates (lexically, newest-looking first), and version-range
    # constraints still reject them in _backtrack /
    # _compatible_with_picked (satisfies() raising counts as no-match).
    parsed: list[tuple[CatalogEntry, Version]] = []
    unparseable: list[CatalogEntry] = []
    for e in entries:
        try:
            parsed.append((e, Version.parse(e.version)))
        except ValueError:
            unparseable.append(e)

    def _sort_key(e: CatalogEntry) -> tuple[Version, int]:
        # Tiebreak on cvc_revision so a newer +cvc.N rebuild of the same
        # upstream version wins over an older, possibly-broken bundle.
        # Version.__lt__ intentionally ignores build metadata (SemVer),
        # so without this the tie was broken by input list order.
        v = Version.parse(e.version)
        return (v, v.cvc_revision)

    if recommended_ver:
        try:
            rv: Version | None = Version.parse(recommended_ver)
        except ValueError:
            rv = None
        rv_rev = rv.cvc_revision if rv is not None else 0
        # Prefer an exact match on cvc_revision when the recommendation
        # carries one; otherwise fall back to base-version equality.
        for e, v in parsed:
            is_match = rv is not None and v == rv and (rv_rev == 0 or v.cvc_revision == rv_rev)
            if is_match and recommended_entry is None:
                recommended_entry = e
            else:
                rest.append(e)
    else:
        rest = [e for e, _ in parsed]

    rest.sort(key=_sort_key, reverse=True)
    rest.extend(sorted(unparseable, key=lambda e: e.version, reverse=True))

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
    for _picked_name, picked_entry in picked.items():
        for dep in picked_entry.required_deps:
            if dep.name == name and dep.version:
                try:
                    if not satisfies(candidate.version, dep.version):
                        return False
                except ValueError:
                    return False
    return True
