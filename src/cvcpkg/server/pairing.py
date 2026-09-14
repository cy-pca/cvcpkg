# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Device-pairing primitives for ``cvcpkg login`` on a headless box — pure.

The pairing flow (RFC 8628 in spirit, hardened) lets a machine with no browser
authenticate: it shows the human a short **user code**, the human approves it in
a browser they already have, and the machine collects a session by polling with
a **client-generated verifier** it never put on the wire in plaintext until
collection.

Everything here is pure and database-free so it can be unit-tested the way
``limits.py`` and ``principals.py`` are.  The database rows and the polling
state machine live in ``cli_auth.py``.

Why the pieces are shaped as they are:

* **The user code** is short enough to read off a terminal and type on a phone,
  drawn from a 30-character alphabet with no ``0/O/1/I/L/U`` so it survives that
  transcription.  8 characters is 30**8 ~= 39 bits — sized against a 600s TTL, a
  per-minute submission cap, and a hard lockout, not against an offline attack.
* **The verifier is client-generated.** The ``pairing_id`` crosses the wire
  twice and may land in a proxy log; on its own it must not collect a
  credential.  The client registers only ``sha256(verifier)`` and presents the
  plaintext only at collection — strictly stronger than RFC 8628, where the
  device code alone suffices.
* **Every secret is HMAC'd under a *separate* derived key.**  One 32-byte file
  signs API tokens, session cookies and these codes; ``derive_key`` keeps a
  forgery primitive in one context from carrying into another.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cvcpkg.server.auth import derive_key

# 30 chars: digits 2-9 and A-Z minus the transcription-ambiguous 0 O 1 I L U.
USER_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
USER_CODE_LEN = 8

# RFC 8628 §3.5: the client must wait at least this long between polls, and add
# this much on every ``slow_down``.
MIN_POLL_INTERVAL = 5
MAX_POLL_INTERVAL = 60


def new_user_code() -> str:
    """A fresh user code, rendered ``XXXX-XXXX`` for readability."""
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(raw: str) -> str | None:
    """Canonicalise a typed code, or ``None`` if it is not a valid shape.

    Upper-cases, drops hyphens/spaces and anything outside the alphabet (so a
    pasted ``7qk4-m2xz`` or ``7QK4 M2XZ`` both work), and accepts only if
    exactly ``USER_CODE_LEN`` alphabet characters remain.  Returns the compact
    (hyphen-free) form used as the lookup key.
    """
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw.upper() if ch in USER_CODE_ALPHABET)
    return cleaned if len(cleaned) == USER_CODE_LEN else None


def new_pairing_id() -> str:
    """An opaque device-side handle for one pairing attempt."""
    return secrets.token_urlsafe(32)


def new_verifier() -> str:
    """The client-generated secret; only ``hash_verifier`` of it is registered."""
    return secrets.token_urlsafe(32)


def hash_user_code(key: bytes, code: str) -> str:
    """Keyed hash of the *normalised* user code (never store the code itself)."""
    norm = normalize_user_code(code) or ""
    return hmac.new(derive_key(key, "usercode"), norm.encode(), hashlib.sha256).hexdigest()


def hash_pairing_id(key: bytes, pairing_id: str) -> str:
    return hmac.new(derive_key(key, "pairing"), pairing_id.encode(), hashlib.sha256).hexdigest()


def hash_verifier(verifier: str) -> str:
    """Plain SHA-256 — the CLIENT computes this too, so it is unkeyed by design."""
    return hashlib.sha256(verifier.encode()).hexdigest()


def verifier_matches(verifier: str, stored_hash: str) -> bool:
    return bool(stored_hash) and hmac.compare_digest(hash_verifier(verifier), stored_hash)


def next_interval(current: int, *, slow_down: bool) -> int:
    """The next poll interval (seconds).

    Steady state holds at *current* (never below the floor); a ``slow_down``
    doubles it, capped.  RFC 8628 §3.5.
    """
    base = max(current, MIN_POLL_INTERVAL)
    if slow_down:
        base *= 2
    return min(base, MAX_POLL_INTERVAL)
