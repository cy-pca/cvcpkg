"""Root-authoritative, top-down catalog resolution (roadmap Phase 12).

In a satellite/edge deployment a client talks to a nearby server that mirrors a
canonical **root** (default ``cvcpkg.org``).  When resolving, the client should
treat the **root as authoritative for the public namespace** — a satellite is a
cache and can never present a divergent public package as authoritative.
**Organization** packages are the inverse: they are local-authoritative (they
live only on the local server, never on the root).

This module provides the pure catalog merge implementing that split:

- **public** packages (no ``org``) come from the **root** catalog;
- **org** packages come from the **local** catalog;
- if the root is unreachable, resolution falls back to the local catalog
  (which carries the satellite's mirror of the public catalog) so an
  air-gapped / offline satellite still works.

Download locality (fetching a root-resolved public archive from the nearer
satellite mirror) is a separate optimization and not done here.
"""

from __future__ import annotations


def _is_public(bundle: dict) -> bool:
    return not bundle.get("org")


def merge_root_authoritative(root_catalog: dict | None, local_catalog: dict | None) -> dict:
    """Merge a root and a local catalog with **root authoritative for public**.

    - ``root_catalog is None`` (root unreachable): return the local catalog
      unchanged — offline fallback to the satellite's mirror.
    - ``local_catalog is None``: return the root catalog (public only).
    - otherwise: **public** bundles from *root*, **org** bundles from *local*.

    Other top-level catalog keys (schema_version, generated_at, …) are taken
    from whichever catalog is the primary in each case (local when both are
    present, so org-side metadata is preserved).
    """
    if root_catalog is None:
        return local_catalog if local_catalog is not None else {"bundles": []}
    if local_catalog is None:
        return root_catalog

    root_bundles = root_catalog.get("bundles", []) or []
    local_bundles = local_catalog.get("bundles", []) or []

    public = [b for b in root_bundles if _is_public(b)]
    org = [b for b in local_bundles if not _is_public(b)]

    merged = dict(local_catalog)
    merged["bundles"] = public + org
    return merged
