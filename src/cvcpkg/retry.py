# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Retry for transient HTTP failures on the install path.

A package server sitting behind a reverse proxy will occasionally hand back a
5xx that has nothing to do with the request: a proxy reusing a backend
connection the app already closed, a worker recycling, a deploy swapping the
container. Measured against production, that is 0.0074% of requests — and it
reddened CI anyway, because a single bad response failed a whole job. A
retry is what converts a rate that low into a non-event.

Deliberately stdlib-only: this runs on the *install* path, which must work
before any dependency of ours is available.

What is and is not retried is the whole design:

* **Retried** — 408, 425, 429, 502, 503, 504, and the connection-level faults
  (reset, timeout, incomplete read -- including a body that ends short of its
  Content-Length, see backends.https -- remote disconnect). These say "ask
  again".
* **Not retried** — every 4xx (the request is wrong; asking again wastes time
  and hides the error) and, pointedly, **500**. A 500 is a deterministic
  application bug: retrying it burns the budget to arrive at the same answer.
* **Never retried here** — TLS certificate verification failures (an
  ``OSError``, so easy to sweep up by accident) and SHA-256 mismatches. A hash
  mismatch is a verdict on the bytes, not a transport hiccup; downloading the
  file again after one is a separate, deliberately small budget owned by
  ``installer.download_bundle``.
* **Only idempotent requests.** GET and HEAD. ``PUT``/``POST`` are untouched:
  re-sending a publish is a correctness question, not a reliability one.

Bounded twice over — by attempt count *and* by a wall-clock budget — so a
fully-down server fails a CI job promptly instead of hanging it.
"""

from __future__ import annotations

import http.client
import logging
import random
import socket
import ssl
import time
import urllib.error
from collections.abc import Callable
from typing import TypeVar

_log = logging.getLogger("cvcpkg")

T = TypeVar("T")

# Statuses worth asking again about. 500 is deliberately absent: see module docs.
TRANSIENT_STATUS: frozenset[int] = frozenset({408, 425, 429, 502, 503, 504})

# Connection-level faults that mean "the transport failed", not "the answer is no".
_TRANSIENT_EXC: tuple[type[BaseException], ...] = (
    ConnectionError,  # includes ConnectionReset/Aborted/Refused
    TimeoutError,
    socket.timeout,
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    ssl.SSLEOFError,
)

DEFAULT_ATTEMPTS = 4
DEFAULT_BUDGET_S = 45.0
DEFAULT_BASE_DELAY_S = 0.5
DEFAULT_MAX_DELAY_S = 8.0
# A server asking us to wait ten minutes is not something CI can honour.
MAX_RETRY_AFTER_S = 30.0


def is_transient(exc: BaseException, _depth: int = 0) -> bool:
    """Should this failure be asked again?

    Follows the ``raise ... from`` chain, because the download path wraps
    everything in ``InstallError``: the transient truth is in the cause, and
    without this the outer retry would never fire on a mid-stream reset.
    """
    # Certificate verification is an OSError, and retrying past it would turn a
    # security failure into a slow security failure.
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_STATUS
    if isinstance(exc, urllib.error.URLError):
        # URLError wraps the real cause (DNS failure, refused connection, ...).
        return isinstance(exc.reason, _TRANSIENT_EXC)
    if isinstance(exc, _TRANSIENT_EXC):
        return True
    # An integrity failure is a verdict on the bytes; never look past it.
    # (Matched by name, subclasses included, to keep this module stdlib-only.)
    # Downloading again after one is a deliberate, separately bounded policy:
    # see installer.download_bundle.
    if any(c.__name__ == "IntegrityError" for c in type(exc).__mro__):
        return False
    if _depth < 4:
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            return is_transient(cause, _depth + 1)
    return False


def is_transient_fetch(exc: BaseException) -> bool:
    """:func:`is_transient`, plus DNS failures: for fetching a recipe's pinned
    upstream source.

    A name-resolution failure is not retried on the install path, where an
    unknown host is as likely a typo as an outage.  A recipe's source URL is
    pinned and has resolved before, so there it is the runner's resolver
    hiccuping: on the 2026-10-14 macOS populate one runner's DNS failed for
    about 40 consecutive downloads ("nodename nor servname provided, or not
    known") from files.pythonhosted.org, github.com and others, and every
    recipe behind them was skipped.  The retry stays bounded by
    :func:`with_retry`'s attempt count and wall-clock budget.
    """
    if is_transient(exc):
        return True
    reason = getattr(exc, "reason", None) if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, socket.gaierror)


def retry_after_seconds(exc: BaseException) -> float | None:
    """The server's own ``Retry-After``, clamped. Only the delta-seconds form
    is honoured; an HTTP-date is ignored rather than half-parsed."""
    hdrs = getattr(exc, "headers", None)
    if hdrs is None:
        return None
    raw = hdrs.get("Retry-After")
    if not raw:
        return None
    try:
        return min(max(float(str(raw).strip()), 0.0), MAX_RETRY_AFTER_S)
    except ValueError:
        return None


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter.

    Jitter is not decoration: a 20-way build matrix that all retried on the
    same schedule would come back in lockstep and rebuild the burst that
    caused the failure.
    """
    ceiling = min(base * (2**attempt), cap)
    return random.uniform(base, ceiling) if ceiling > base else base  # noqa: S311


def with_retry(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = DEFAULT_ATTEMPTS,
    budget_s: float = DEFAULT_BUDGET_S,
    base_delay_s: float = DEFAULT_BASE_DELAY_S,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
    transient: Callable[[BaseException], bool] = is_transient,
) -> T:
    """Call ``fn``, retrying transient failures. Re-raises the last error.

    ``what`` appears in the log line, so a flaky server is visible in CI output
    rather than silently smoothed over — the point is to survive the blip AND
    leave evidence it happened.  ``transient`` decides what is worth asking
    again (default :func:`is_transient`).
    """
    started = time.monotonic()
    last: BaseException | None = None

    for attempt in range(attempts):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 -- re-raised below
            if not transient(exc):
                raise
            last = exc
            if attempt == attempts - 1:
                break
            wait = retry_after_seconds(exc)
            if wait is None:
                wait = backoff_delay(attempt, base_delay_s, max_delay_s)
            elapsed = time.monotonic() - started
            if elapsed + wait >= budget_s:
                _log.warning(
                    "%s: %s -- retry budget (%.0fs) exhausted after %d attempt(s)",
                    what,
                    exc,
                    budget_s,
                    attempt + 1,
                )
                break
            _log.warning(
                "%s: %s -- retrying in %.1fs (attempt %d/%d)",
                what,
                exc,
                wait,
                attempt + 2,
                attempts,
            )
            time.sleep(wait)

    assert last is not None
    raise last
