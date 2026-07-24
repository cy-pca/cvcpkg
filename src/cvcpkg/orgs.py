# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Organization helpers usable by both client and server.

Deliberately dependency-free: the CLI validates org slugs before publishing
(``cvcpkg publish --org``), and a base install must not require the server
extras (pydantic et al.) for that. Server code re-exports these via
``cvcpkg.server.models`` for backward compatibility.
"""

from __future__ import annotations

import re

_ORG_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_CONSECUTIVE_HYPHENS_RE = re.compile(r"--")


def served_set(home: str, extra=None) -> list[str]:
    """Normalize a builder's served-namespace set.

    The result always contains *home* (the builder's own org / identity) and
    then each namespace in *extra*, order-stable and de-duplicated. ``""`` is
    the public namespace. Shared by the builder agent (registration payload)
    and the server (row normalization) so both compute the same set.
    """
    result: list[str] = [home]
    for ns in extra or []:
        if ns not in result:
            result.append(ns)
    return result


def validate_org_slug(slug: str) -> str | None:
    """Validate an organization slug using GitHub username rules.

    Rules (matching GitHub):
    - 1–39 characters
    - Only lowercase alphanumeric characters or hyphens
    - Cannot start or end with a hyphen
    - No consecutive hyphens

    Returns ``None`` on success or an error message string on failure.
    """
    if not slug:
        return "organization slug must not be empty"
    if len(slug) > 39:
        return f"organization slug must be at most 39 characters (got {len(slug)})"
    if _CONSECUTIVE_HYPHENS_RE.search(slug):
        return "organization slug must not contain consecutive hyphens"
    if not _ORG_SLUG_RE.match(slug):
        return (
            "organization slug may only contain lowercase alphanumeric "
            "characters or hyphens, and cannot start or end with a hyphen"
        )
    return None
