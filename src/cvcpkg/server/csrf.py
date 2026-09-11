# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Per-form CSRF tokens for the cookie-authenticated HTML surfaces.

The session cookie is ``SameSite=Lax``, which stops a cross-site *form POST*
in current browsers — but it is a browser-version-dependent mitigation, it does
not cover a same-site subdomain, and the dashboard already ships mutating POSTs
(create a token, revoke a token, act on a package) that have no second factor
at all.  So Lax is treated here as defence in depth **under** a token, never as
the defence.

Two properties beyond a bare session-bound token:

* **Bound to the form, not just the session.** The token commits to a ``ref``
  naming the form's purpose, so one lifted from a low-stakes form (say
  ``account-rename``) cannot be replayed against ``principal-disable``.
* **Only required of cookie-authenticated requests.** A browser never attaches
  ``Authorization:`` on its own, so a bearer-token API call is not reachable by
  CSRF and must not be made to carry a token it has no way to obtain.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from cvcpkg.server.auth import derive_key

#: Form purposes.  Kept as constants so a typo is an AttributeError at import
#: time rather than a token that silently never validates.
REF_ACCOUNT_MINT = "account-token-mint"
REF_ACCOUNT_REVOKE = "account-token-revoke"
REF_ACCOUNT_REPLACE = "account-token-replace"
REF_ACCOUNT_RENAME = "account-rename"
REF_SESSION_REVOKE = "session-revoke"
REF_SESSION_REVOKE_ALL = "session-revoke-all"
REF_LOGOUT = "logout"
REF_ADMIN_TOKEN_CREATE = "admin-token-create"
REF_ADMIN_TOKEN_REVOKE = "admin-token-revoke"
REF_ADMIN_PACKAGE_ACTION = "admin-package-action"
REF_PRINCIPAL_DISABLE = "principal-disable"
REF_PRINCIPAL_ENABLE = "principal-enable"
REF_PRINCIPAL_REVOKE_SESSIONS = "principal-revoke-sessions"

FIELD = "_csrf"


def issue(hmac_key: bytes, sid: str, ref: str) -> str:
    """Return the CSRF token for session *sid* acting on form *ref*."""
    msg = f"{sid}:{ref}".encode()
    return hmac.new(derive_key(hmac_key, "csrf"), msg, hashlib.sha256).hexdigest()


def check(hmac_key: bytes, sid: str, ref: str, presented: str) -> bool:
    """Constant-time comparison of a presented token against the expected one."""
    if not presented or not sid:
        return False
    return hmac.compare_digest(issue(hmac_key, sid, ref), presented)


def origin_ok(request, *, public_url: str = "") -> bool:
    """True if the request's Origin/Referer is this site, or absent.

    A second, independent check: even a forged token is useless if the request
    demonstrably came from somewhere else.  Absent headers pass — a same-origin
    form POST may legitimately send neither, and failing closed here would
    break ordinary browsers to no benefit, since the token still has to match.
    """
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""
    candidate = origin or referer
    if not candidate:
        return True

    try:
        got = urlsplit(candidate)
    except ValueError:
        return False
    if not got.hostname:
        return False

    hosts = {(request.headers.get("host") or "").split(":")[0].lower()}
    if public_url:
        try:
            hosts.add((urlsplit(public_url).hostname or "").lower())
        except ValueError:
            pass
    hosts.discard("")
    return got.hostname.lower() in hosts
