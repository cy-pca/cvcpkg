"""The builder's dependency downloads retry transport errors and transient statuses."""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from cvcpkg.cli._builder import _DEP_GET_ATTEMPTS, _get_retrying  # noqa: E402


class _Client:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _disconnect():
    return httpx.RemoteProtocolError("Server disconnected without sending a response.")


def test_retries_a_dropped_keepalive_connection():
    ok = httpx.Response(200, json={"packages": []})
    client = _Client([_disconnect(), httpx.ConnectError("refused"), ok])
    slept = []
    resp = _get_retrying(client, "https://s/v1/packages/x", headers={"A": "b"}, sleep=slept.append)
    assert resp is ok
    assert len(client.calls) == 3
    assert all(kw == {"headers": {"A": "b"}} for _, kw in client.calls)
    assert len(slept) == 2


def test_gives_up_after_the_last_attempt():
    client = _Client([_disconnect() for _ in range(_DEP_GET_ATTEMPTS)])
    with pytest.raises(httpx.RemoteProtocolError):
        _get_retrying(client, "https://s/a.tar.zst", sleep=lambda _s: None)
    assert len(client.calls) == _DEP_GET_ATTEMPTS


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_a_definite_http_error_status_is_not_retried(status):
    err = httpx.Response(status)
    client = _Client([err])
    assert _get_retrying(client, "https://s/a.tar.zst", sleep=lambda _s: None) is err
    assert len(client.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_transient_http_error_status_is_retried(status):
    # pango on Windows (2026-10-20) lost fribidi to one of these: the lookup's
    # error status was taken for "not on the server".
    ok = httpx.Response(200, json={"packages": []})
    client = _Client([httpx.Response(status), ok])
    slept = []
    assert _get_retrying(client, "https://s/v1/packages/x", sleep=slept.append) is ok
    assert len(client.calls) == 2
    assert len(slept) == 1


def test_a_transient_status_on_the_last_attempt_is_returned():
    last = httpx.Response(502)
    client = _Client([httpx.Response(503)] * (_DEP_GET_ATTEMPTS - 1) + [last])
    assert _get_retrying(client, "https://s/a", sleep=lambda _s: None) is last
    assert len(client.calls) == _DEP_GET_ATTEMPTS


def test_timeouts_are_retried():
    ok = httpx.Response(200)
    client = _Client([httpx.ReadTimeout("slow"), ok])
    assert _get_retrying(client, "https://s/a", sleep=lambda _s: None) is ok
