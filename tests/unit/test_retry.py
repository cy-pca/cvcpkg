# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Retry policy on the install path.

The point of these is less "does it retry" than "does it retry the RIGHT
things" — a retry that swallows a 404, a certificate failure or a hash
mismatch is worse than no retry at all.
"""

from __future__ import annotations

import http.client
import ssl
import urllib.error

import pytest

from cvcpkg.errors import InstallError, IntegrityError
from cvcpkg.retry import (
    DEFAULT_ATTEMPTS,
    is_transient,
    retry_after_seconds,
    with_retry,
)


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/y", code, "err", headers or {}, None)  # type: ignore[arg-type]


# ── what counts as transient ────────────────────────────────────────────────


@pytest.mark.parametrize("code", [408, 425, 429, 502, 503, 504])
def test_transient_statuses_are_retried(code):
    assert is_transient(_http_error(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 410, 413, 422])
def test_client_errors_are_never_retried(code):
    assert not is_transient(_http_error(code))


def test_500_is_not_retried():
    """A 500 is a deterministic application bug. Retrying it burns the budget
    to arrive at the same answer, and hides the bug behind latency."""
    assert not is_transient(_http_error(500))


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError("reset"),
        ConnectionRefusedError("refused"),
        TimeoutError("timeout"),
        http.client.IncompleteRead(b"partial"),
        http.client.RemoteDisconnected("closed"),
    ],
)
def test_connection_faults_are_retried(exc):
    assert is_transient(exc)


def test_url_error_wrapping_a_reset_is_retried():
    assert is_transient(urllib.error.URLError(ConnectionResetError("reset")))


def test_url_error_wrapping_a_name_failure_is_not():
    assert not is_transient(urllib.error.URLError(OSError("name or service not known")))


def test_certificate_failures_are_never_retried():
    """SSLCertVerificationError is an OSError, so it is easy to sweep up by
    accident. Retrying past it turns a security failure into a slow one."""
    assert not is_transient(ssl.SSLCertVerificationError("bad cert"))


def test_integrity_failure_is_never_retried_even_with_a_transient_cause():
    """A hash mismatch is a verdict on the BYTES. Even if the transfer that
    produced them also hiccuped, asking again must be a deliberate decision."""
    err = IntegrityError("sha256 mismatch")
    err.__cause__ = ConnectionResetError("reset")
    assert not is_transient(err)


def test_transience_is_read_through_the_cause_chain():
    """The download path wraps everything in InstallError; without following
    `raise ... from`, the outer retry would never fire on a mid-stream reset."""
    wrapped = InstallError("failed to download http://x/y: reset")
    wrapped.__cause__ = ConnectionResetError("reset")
    assert is_transient(wrapped)

    benign = InstallError("no archive_url for foo==1.0")
    assert not is_transient(benign)


def test_cause_chain_traversal_is_bounded():
    """A cyclic or absurdly deep chain must not hang or recurse away."""
    a, b = InstallError("a"), InstallError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert is_transient(a) is False


# ── Retry-After ─────────────────────────────────────────────────────────────


def test_retry_after_is_honoured_and_clamped():
    assert retry_after_seconds(_http_error(503, {"Retry-After": "2"})) == 2.0
    # a server asking for ten minutes is not something CI can honour
    assert retry_after_seconds(_http_error(503, {"Retry-After": "600"})) == 30.0
    # an HTTP-date form is ignored rather than half-parsed
    assert (
        retry_after_seconds(_http_error(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
        is None
    )
    assert retry_after_seconds(_http_error(503)) is None


# ── the loop ────────────────────────────────────────────────────────────────


def test_succeeds_without_retrying(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []
    assert with_retry(lambda: calls.append(1) or "ok", what="t") == "ok"
    assert len(calls) == 1


def test_recovers_from_a_transient_failure(monkeypatch):
    """The scenario that reddened CI: one bad response, then a good one."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(502)
        return "ok"

    assert with_retry(flaky, what="t") == "ok"
    assert len(calls) == 2


def test_gives_up_after_the_attempt_limit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []

    def always_502():
        calls.append(1)
        raise _http_error(502)

    with pytest.raises(urllib.error.HTTPError):
        with_retry(always_502, what="t")
    assert len(calls) == DEFAULT_ATTEMPTS


def test_a_non_transient_failure_raises_immediately(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []

    def not_found():
        calls.append(1)
        raise _http_error(404)

    with pytest.raises(urllib.error.HTTPError):
        with_retry(not_found, what="t")
    assert len(calls) == 1, "a 404 must not be asked again"


def test_the_wall_clock_budget_bounds_a_down_server(monkeypatch):
    """Attempts alone are not enough: with Retry-After honoured, a server could
    otherwise stretch a job by minutes. CI must fail promptly."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    clock = {"t": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])

    def always_busy():
        clock["t"] += 20.0  # each attempt burns 20s of the budget
        raise _http_error(503)

    with pytest.raises(urllib.error.HTTPError):
        with_retry(always_busy, what="t", budget_s=45.0)
    assert sum(slept) < 45.0


def test_the_last_error_is_the_one_raised(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    seq = [502, 503, 504, 502]
    it = iter(seq)

    def failing():
        raise _http_error(next(it))

    with pytest.raises(urllib.error.HTTPError) as ei:
        with_retry(failing, what="t")
    assert ei.value.code == seq[-1]


def test_retries_are_logged_so_a_flaky_server_is_visible(monkeypatch, caplog):
    """Surviving the blip is half the job; leaving evidence is the other half."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(502)
        return "ok"

    with caplog.at_level("WARNING", logger="cvcpkg"):
        with_retry(flaky, what="GET http://x/y")
    assert any(
        "retrying" in r.message.lower() or "retrying" in r.getMessage().lower()
        for r in caplog.records
    )
