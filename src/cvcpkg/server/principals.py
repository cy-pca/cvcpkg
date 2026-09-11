# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Naming rules for SSO principals — pure, no database.

A **principal** is a human identity that signed in through the identity
provider.  Its ``name`` is not cosmetic: cvcpkg keys organization membership on
a bare string (``org_members.token_name``), so the name a principal is allotted
is exactly what grants — or fails to grant — access to a private org.  That
makes allocation a security boundary, and it is why this module is separated
out and unit-tested without a database, in the manner of ``limits.py``.

Two rules follow from that and are enforced by the caller
(``identities.DbPrincipalStore``), not here:

* a principal is resolved on ``(issuer, subject)`` and **never** on email;
* a name already claimed by any token — *including a revoked one* — is not
  available, because revoking a token does not remove its membership rows.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

from cvcpkg.server.models import TokenRole

# Names the server itself writes, or that would be confusing to hand out.  The
# last three are literal audit actors the server records (``admin-ui`` at the
# dashboard login, ``oidc-user`` when a subject cannot be derived, and
# ``retention-gc``, which holds no token at all) — a principal owning one of
# those names would make the audit log ambiguous about who acted.
RESERVED = frozenset(
    {
        "admin",
        "root",
        "system",
        "cvcpkg",
        "cvc",
        "support",
        "security",
        "api",
        "www",
        "help",
        "abuse",
        "postmaster",
        "null",
        "none",
        "admin-ui",
        "oidc-user",
        "retention-gc",
    }
)

# Mirrors app._C_IDENTIFIER_RE: must start with a letter or underscore, then
# letters, digits, underscores or hyphens.  Note there is no "." in either
# class — that is load-bearing, because a minted token is named
# "<principal>.<label>" and the dot is what keeps a label from ever being
# mistaken for, or colliding with, a principal name.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_STRIP_RE = re.compile(r"[^A-Za-z0-9_\-]")

# A token label: the part after the dot in "<principal>.<label>".
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

_MAX_NAME = 255

_ROLE_RANK = {TokenRole.reader: 0, TokenRole.publisher: 1, TokenRole.admin: 2}


def min_role(a: TokenRole, b: TokenRole) -> TokenRole:
    """The lesser of two roles.

    Used to clamp a delegated token to its principal's *current* entitlement,
    so demoting someone in the identity provider takes effect on their next
    request rather than whenever their token happens to expire.
    """
    return a if _ROLE_RANK.get(a, 0) <= _ROLE_RANK.get(b, 0) else b


def is_valid_name(name: str) -> bool:
    """True if *name* is allocatable as a principal name."""
    return bool(name) and len(name) <= _MAX_NAME and bool(_NAME_RE.match(name))


def validate_label(label: str) -> bool:
    """True if *label* is usable as the suffix of a minted token name."""
    return bool(_LABEL_RE.match(label))


def _from_sub(subject: str) -> str:
    """A stable, always-valid fallback name derived from the OIDC subject."""
    digest = hashlib.sha1(subject.encode("utf-8", "replace")).hexdigest()[:12]
    return f"u{digest}"


def sanitize_principal_name(claims: dict) -> str:
    """Derive a candidate principal name from IdP claims.

    Preference order: ``preferred_username``, then the local part of ``email``,
    then a digest of ``sub``.  **Total** — never raises and never returns the
    empty string, because every caller is in the middle of a login that has
    already succeeded and refusing at this point would strand a user who did
    nothing wrong.  A display name that is entirely non-ASCII sanitizes away to
    nothing, which is exactly why the digest fallback is unconditional rather
    than a last resort.
    """
    subject = str(claims.get("sub") or "")

    for raw in (
        claims.get("preferred_username"),
        str(claims.get("email") or "").split("@", 1)[0],
    ):
        candidate = _STRIP_RE.sub("", str(raw or "")).lower()[:_MAX_NAME]
        # A leading digit or hyphen fails _NAME_RE; prefixing beats rejecting,
        # since "9lives" is a perfectly reasonable thing to be called.
        if candidate and not _NAME_RE.match(candidate):
            candidate = f"u{candidate}"[:_MAX_NAME]
        if candidate and is_valid_name(candidate):
            return candidate

    return _from_sub(subject)


def collision_candidates(base: str) -> Iterator[str]:
    """Yield names to try for *base*: ``base``, ``base-2``, ``base-3``, …

    Capped: after ``base-99`` it switches to a digest suffix rather than
    counting forever, so a pathological base cannot turn a login into a long
    scan of the tokens table.
    """
    yield base
    for n in range(2, 100):
        yield f"{base}-{n}"[:_MAX_NAME]
    seed = base
    for n in range(8):
        seed = hashlib.sha1(f"{seed}:{n}".encode()).hexdigest()[:6]
        yield f"{base}-{seed}"[:_MAX_NAME]


def token_name_for(principal_name: str, label: str) -> str:
    """The row name of a token minted by *principal_name* under *label*.

    The dot is deliberate and is the reason neither character class above
    permits one: a token name always carries its owning principal as a literal
    prefix, and can never be confused with a principal name.
    """
    return f"{principal_name}.{label}"


def principal_of_token_name(token_name: str) -> str:
    """The principal prefix of a minted token name, or '' for a bare token."""
    head, sep, _tail = token_name.partition(".")
    return head if sep else ""
