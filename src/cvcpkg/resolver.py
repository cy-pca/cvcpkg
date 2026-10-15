# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Backtracking SAT-style resolver for component bundles.

See docs/roadmap/split-distribution.md §5.5 for the algorithm
description.  The resolver searches the catalog for a consistent
assignment of bundle versions that satisfies all user requirements
and inter-component dependency constraints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from cvcpkg.errors import ResolveError
from cvcpkg.manifest import CatalogEntry, ComponentReq
from cvcpkg.semver import Version, satisfies, version_sort_key


@dataclass
class ResolveResult:
    """Successful resolution: a mapping of component name → chosen entry."""

    picked: dict[str, CatalogEntry] = field(default_factory=dict)
    # Names satisfied outside the catalog by *local_only*: recipes the
    # installer builds from source, dependencies before their dependents.
    local: list[str] = field(default_factory=list)


def resolve(
    requirements: list[ComponentReq],
    candidates: dict[str, list[CatalogEntry]],
    *,
    recommended: dict[str, str] | None = None,
    capabilities: set[str] | None = None,
    local_only: Callable[[str], list[str] | None] | None = None,
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
    local_only:
        Consulted only for a needed name with no catalog candidate at all.
        Returns that name's own runtime dependencies when it can be built
        locally instead -- a ``redistributable: false`` recipe, which is
        never in the catalog -- or ``None`` when it cannot.  Such a name
        lands in ``ResolveResult.local`` and its dependencies are resolved
        like any other, so a catalog bundle may depend on one.

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
    trail = _Trail()
    local: dict[str, list[str]] = {}

    lookup: Callable[[str], list[str] | None] | None = None
    if local_only is not None:
        # Memoized: backtracking may ask about the same name many times, and
        # the answer (a recipe on disk) cannot change mid-resolve.
        memo: dict[str, list[str] | None] = {}

        def _memoized(name: str) -> list[str] | None:
            if name not in memo:
                memo[name] = local_only(name)
            return memo[name]

        lookup = _memoized

    failure = _backtrack(
        needed,
        constraints,
        sorted_candidates,
        picked,
        trail,
        capability_blocked,
        local_only=lookup,
        local=local,
    )
    if failure is not None:
        raise ResolveError("cannot satisfy requirements:\n  " + "\n  ".join(trail.report()))

    return ResolveResult(picked=picked, local=_deps_first(local))


class _Trail:
    """Why the search failed, kept per component rather than per attempt.

    Every failed attempt used to append a line, so an unsatisfiable closure
    grew the list without bound, and the error showed whichever ten lines came
    last -- usually components that resolve fine.  Here each component keeps
    its latest reason, and the report leads with components that have no
    bundle at all for this platform, since those are the usual root cause.
    """

    _MAX_LINES = 10

    def __init__(self) -> None:
        # name -> (reason, requirers); insertion order is first-failure order.
        self._missing: dict[str, tuple[str, frozenset[str]]] = {}
        self._failed: dict[str, tuple[str, frozenset[str]]] = {}

    def missing(self, name: str, reason: str, requirers: frozenset[str]) -> None:
        self._missing[name] = (reason, requirers)

    def failed(self, name: str, reason: str, requirers: frozenset[str]) -> None:
        self._failed[name] = (reason, requirers)

    def report(self) -> list[str]:
        lines: list[str] = []
        for table in (self._missing, self._failed):
            for name, (reason, requirers) in table.items():
                if table is self._failed and name in self._missing:
                    continue
                by = f" -- required by {', '.join(sorted(requirers))}" if requirers else ""
                lines.append(reason + by)
        if len(lines) > self._MAX_LINES:
            extra = len(lines) - self._MAX_LINES
            lines = lines[: self._MAX_LINES] + [f"... and {extra} more"]
        return lines


def _deps_first(local: dict[str, list[str]]) -> list[str]:
    """Order *local* (name -> its deps) so each name follows its local deps."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for dep in local[name]:
            if dep in local:
                visit(dep)
        order.append(name)

    for name in sorted(local):
        visit(name)
    return order


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
    # Most-specific-wins on platform, applied WITHIN a version: a bundle built
    # for this host's own platform beats a noarch one carrying the same version.
    # Both satisfy the host (platform_matches lets `any` through deliberately),
    # but they are not interchangeable -- a recipe that ships both does so
    # because the noarch build cannot serve every target.  For the pure-Python
    # columns that means layout: the noarch bundle installs to
    # lib/pythonX.Y/site-packages, while Windows needs Lib/site-packages, so
    # letting the noarch bundle win on Windows installs files where that
    # interpreter will never look.  Version still dominates, so this only
    # breaks ties rather than pinning a host to an older revision.
    ordered = sorted(
        entries,
        key=lambda e: (
            version_sort_key(e.version, e.cvc_revision),
            e.platform != "any",
        ),
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
    trail: _Trail,
    capability_blocked: dict[str, set[str]] | None = None,
    *,
    local_only: Callable[[str], list[str] | None] | None = None,
    local: dict[str, list[str]] | None = None,
    required_by: dict[str, frozenset[str]] | None = None,
) -> frozenset[str] | None:
    """Recursive resolver with conflict-directed backjumping.

    Returns ``None`` on success.  On failure it returns the *conflict set*:
    the picked names whose choices could have caused it.  *required_by* maps
    each needed name to the picks that required or constrained it, so a dead
    end's conflict set is the picks above it on its dependency path.  A pick
    that is not in the set returns it at once instead of trying its other
    candidates -- none of them can change the outcome.  Without this, one
    component with no bundle for the platform made the search retry every
    combination of versions of everything picked before it.
    """
    capability_blocked = capability_blocked or {}
    if local is None:
        local = {}
    if required_by is None:
        required_by = {}
    # Find the next unpicked component.
    unpicked = needed - picked.keys() - local.keys()
    if not unpicked:
        return None

    name = min(unpicked)  # deterministic order
    available = candidates.get(name, [])
    spec = constraints.get(name, "")
    requirers = required_by.get(name, frozenset())
    # Whatever required or constrained this name shares the blame for any
    # failure here: a different pick there might not need it at all.
    conflict: set[str] = set(requirers)

    # Nothing in the catalog can ever be named this (a capability-filtered
    # pool is a different failure): a `redistributable: false` recipe is
    # satisfied by building it locally, and its own deps join the search.
    if not available and name not in capability_blocked and local_only is not None:
        deps = local_only(name)
        if deps is not None:
            local[name] = deps
            sub = _backtrack(
                needed | set(deps),
                constraints,
                candidates,
                picked,
                trail,
                capability_blocked,
                local_only=local_only,
                local=local,
                required_by=_add_requirer(required_by, name, deps),
            )
            if sub is None:
                return None
            del local[name]
            conflict |= sub - {name}
            return frozenset(conflict)

    rejections: list[str] = []
    tried = False
    for entry in available:
        # Check the version constraint (user-level and from the requirers).
        if spec:
            try:
                ok = satisfies(entry.version, spec)
            except ValueError:
                ok = False
            if not ok:
                continue

        # Check compatibility with the picks already made, in both directions.
        clash, why = _conflicts_with_picked(name, entry, picked)
        if clash:
            conflict |= clash
            if len(rejections) < 3:
                rejections.append(why)
            continue

        # Tentatively pick this candidate.
        tried = True
        picked[name] = entry

        # Add transitive dependencies to the needed set.
        new_needed = set(needed)
        new_constraints = dict(constraints)
        dep_names = []
        for dep in entry.required_deps:
            new_needed.add(dep.name)
            dep_names.append(dep.name)
            if dep.version:
                existing = new_constraints.get(dep.name, "")
                if existing and existing != dep.version:
                    # Two different constraints on the same dep — take the intersection
                    # by combining them with a comma.
                    new_constraints[dep.name] = f"{existing},{dep.version}"
                else:
                    new_constraints[dep.name] = dep.version

        sub = _backtrack(
            new_needed,
            new_constraints,
            candidates,
            picked,
            trail,
            capability_blocked,
            local_only=local_only,
            local=local,
            required_by=_add_requirer(required_by, name, dep_names),
        )
        if sub is None:
            return None

        # Backtrack.
        del picked[name]
        if name not in sub:
            # Backjump: the failure below does not depend on this pick.
            return sub
        conflict |= sub - {name}

    if not tried:
        # Record only direct failures: a name whose candidates were all
        # rejected here.  A name that failed through its dependencies has
        # its cause recorded further down.
        if not available and name in capability_blocked:
            missing = ", ".join(sorted(capability_blocked[name]))
            trail.missing(
                name,
                f"no provider for '{name}' meets required capabilities (host is missing: {missing})",
                requirers,
            )
        elif not available:
            trail.missing(
                name,
                f"no candidate for '{name}' satisfies constraints (spec={spec!r}): "
                "no bundle for this platform",
                requirers,
            )
        else:
            detail = f": {'; '.join(rejections)}" if rejections else ""
            trail.failed(
                name,
                f"no candidate for '{name}' satisfies constraints (spec={spec!r}){detail}",
                requirers,
            )
    return frozenset(conflict)


def _add_requirer(
    required_by: dict[str, frozenset[str]], requirer: str, deps: list[str]
) -> dict[str, frozenset[str]]:
    """Return *required_by* with *requirer* added for each of *deps*."""
    if not deps:
        return required_by
    out = dict(required_by)
    for dep in deps:
        out[dep] = out.get(dep, frozenset()) | {requirer}
    return out


def _conflicts_with_picked(
    name: str,
    candidate: CatalogEntry,
    picked: dict[str, CatalogEntry],
) -> tuple[set[str], str]:
    """Picks that rule out *candidate* for *name*, and why.

    Both directions: a picked bundle whose dependency constraint on *name*
    rejects the candidate, and a picked bundle that the candidate's own
    constraints reject.  The second was never checked, so a dependency
    picked before its dependent (names go in alphabetical order) kept a
    version the dependent does not accept.
    """
    clash: set[str] = set()
    why = ""
    for picked_name, picked_entry in picked.items():
        for dep in picked_entry.required_deps:
            if dep.name == name and dep.version and not _satisfies(candidate.version, dep.version):
                clash.add(picked_name)
                why = why or f"{picked_name}=={picked_entry.version} needs {name} {dep.version}"
    for dep in candidate.required_deps:
        held = picked.get(dep.name)
        if held is not None and dep.version and not _satisfies(held.version, dep.version):
            clash.add(dep.name)
            why = why or (
                f"{name}=={candidate.version} needs {dep.name} {dep.version} "
                f"(picked {held.version})"
            )
    return clash, why


def _satisfies(version: str, spec: str) -> bool:
    """``satisfies`` with an unparseable version or spec counting as no match."""
    try:
        return satisfies(version, spec)
    except ValueError:
        return False
