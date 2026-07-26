# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Compute a fresh ``cvc_revision`` at pack time.

Today, republishing a package requires hand-editing ``cvc_revision`` in
``recipe.yaml`` and committing it (see the libcvc "family revision bump"
commits).  This is manual and easy to get wrong: forget the edit and pack
emits the already-published version, which ``publish`` silently skips (409),
so the new content never lands.

``cvcpkg pack --bump`` automates the increment.  It asks the publish server
what is already live for a package and packs one revision above it, so the
bundle is guaranteed fresh without touching the recipe.  The recipe's
committed ``cvc_revision`` acts as a *floor*, honored when it is ahead of the
server (e.g. a deliberate manual bump) or when nothing is published yet.

Policy (docs/roadmap/revision-bump-cascade.md): a published bundle identity is
immutable, so new content MUST carry a strictly higher ``+cvc.N``.  A new
upstream revision can break downstream, so downstream rebuilds and republishes
against it with its own new revision — hence the cascade helpers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

_CVC_RE = re.compile(r"\+cvc\.(\d+)")


def revision_of(version: str) -> int:
    """Extract the ``+cvc.<N>`` revision from a version string, or 0.

    ``"3.2.4+cvc.5"`` → ``5``; ``"3.2.4"`` (no suffix) → ``0``.
    """
    m = _CVC_RE.search(version or "")
    return int(m.group(1)) if m else 0


def base_version(version: str) -> str:
    """Return the upstream part of a version, stripping ``+cvc.<N>``.

    ``"3.2.4+cvc.5"`` → ``"3.2.4"``.  Build metadata (everything after the
    first ``+``) identifies the CVC revision, not the upstream release, so a
    new upstream version starts its ``+cvc`` counter fresh.
    """
    return (version or "").split("+", 1)[0]


def next_revision(recipe_revision: int, published: Iterable[int]) -> int:
    """The revision to pack so ``publish`` will not 409.

    * ``recipe_revision`` — the committed floor from ``recipe.yaml``.
    * ``published`` — every revision already live on the server for this
      package's *current upstream version* (empty on the first publish).

    Returns the recipe floor when nothing is published (first publish lands
    at the recipe's own revision, unbumped), otherwise one above the highest
    published revision — but never below the recipe floor, so a manually
    bumped recipe is always honored.
    """
    highest = -1
    for rev in published:
        if rev > highest:
            highest = rev
    if highest < 0:
        return recipe_revision
    return max(recipe_revision, highest + 1)


def fetch_published_revisions(
    name: str,
    *,
    server: str,
    org: str = "",
    token: str = "",
    upstream_version: str = "",
    platform: str = "",
    arch: str = "",
    build_type: str = "",
    link: str = "",
    scope: str = "name",
    timeout: float = 30.0,
) -> list[int]:
    """Return every published ``+cvc.N`` for *name* on *server*.

    Queries ``GET /v1/packages/{name}`` — the same endpoint the publish
    duplicate pre-check uses — and reads the revision off each bundle's
    ``version``.

    * ``upstream_version`` — when set, only bundles on this exact upstream
      release count, so a new upstream version's revision counter starts
      fresh instead of inheriting the previous release's high-water mark.
    * ``scope`` — ``"name"`` (default) counts every variant of the package so
      a family bump stays uniform across platforms and configs (matching the
      manual family-revision-bump convention); ``"variant"`` restricts to the
      given platform/arch/build_type/link tuple.
    * ``org`` — when set, only this org's bundles count; a different org's
      identical variant must not raise our floor.

    Yanked bundles are included: the server's uniqueness constraint still
    holds their version, so reusing it would 409.  Returns ``[]`` when the
    package is unknown or the server is unreachable — the caller then falls
    back to the recipe floor.
    """
    import httpx

    base = server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params: dict[str, object] = {"limit": 500, "include_yanked": "true"}
    if org:
        params["org"] = org
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base}/v1/packages/{name}", params=params, headers=headers)
        if resp.status_code != 200:
            return []
        packages = resp.json().get("packages", [])
    except Exception:
        return []

    revs: list[int] = []
    for pkg in packages:
        version = pkg.get("version", "")
        if upstream_version and base_version(version) != upstream_version:
            continue
        if org and pkg.get("org", "") != org:
            continue
        if scope == "variant":
            if (
                (platform and pkg.get("platform") != platform)
                or (arch and pkg.get("arch") != arch)
                or (build_type and pkg.get("build_type") != build_type)
                or (link and pkg.get("link") != link)
            ):
                continue
        revs.append(revision_of(version))
    return revs


def compute_pack_revision(
    recipe: object,
    *,
    server: str,
    org: str = "",
    token: str = "",
    platform: str = "",
    arch: str = "",
    build_type: str = "",
    link: str = "",
    scope: str = "name",
    log: Callable[[str], None] | None = None,
) -> int:
    """Resolve the revision to pack *recipe* at under ``--bump``.

    Falls back to the recipe's committed revision when *server* is empty or
    unreachable (offline / ``--local``-style use still produces a valid, if
    unbumped, bundle).  *recipe* is any object exposing ``name``,
    ``upstream_version`` and ``cvc_revision`` (a :class:`cvcpkg.builder.Recipe`).
    """
    floor = int(recipe.cvc_revision)  # type: ignore[attr-defined]
    if not server:
        return floor
    published = fetch_published_revisions(
        recipe.name,  # type: ignore[attr-defined]
        server=server,
        org=org,
        token=token,
        upstream_version=str(recipe.upstream_version),  # type: ignore[attr-defined]
        platform=platform,
        arch=arch,
        build_type=build_type,
        link=link,
        scope=scope,
    )
    new = next_revision(floor, published)
    if log is not None:
        name = recipe.name  # type: ignore[attr-defined]
        if published:
            log(
                f"cvcpkg: {name}: highest published +cvc.{max(published)} "
                f"({len(published)} bundle(s)); packing +cvc.{new}"
            )
        else:
            log(f"cvcpkg: {name}: nothing published for this upstream; packing +cvc.{new}")
    return new
