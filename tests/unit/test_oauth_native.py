# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The stdlib client login flows in ``oauth_native`` against a stub broker."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from cvcpkg import oauth_native


def _pkce_ok(verifier: str, challenge: str) -> bool:
    import base64
    import hashlib

    want = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return want == challenge


def test_pkce_pair_is_valid_and_unique():
    v1, c1 = oauth_native._pkce()
    v2, c2 = oauth_native._pkce()
    assert _pkce_ok(v1, c1) and _pkce_ok(v2, c2)
    assert v1 != v2 and c1 != c2


def test_can_open_browser(monkeypatch):
    monkeypatch.setattr(oauth_native.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert oauth_native.can_open_browser() is False
    monkeypatch.setenv("DISPLAY", ":0")
    assert oauth_native.can_open_browser() is True
    monkeypatch.setattr(oauth_native.sys, "platform", "darwin")
    assert oauth_native.can_open_browser() is True


class _StubBroker:
    """A minimal broker: device start/poll + token exchange."""

    def __init__(self, *, pending_polls=1, deny=False, slow_down_once=False):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._pending = pending_polls
        self._deny = deny
        self._slow = slow_down_once
        self.token_calls = []
        broker = self

        class H(BaseHTTPRequestHandler):
            def _json(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                ln = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(ln).decode()
                path = urlsplit(self.path).path
                if path == "/v1/auth/device":
                    self._json(
                        200,
                        {
                            "pairing_id": "pid-123",
                            "user_code": "7QK4-M2XZ",
                            "verification_uri": f"{broker.url}/link",
                            "verification_uri_complete": f"{broker.url}/link?code=7QK4-M2XZ",
                            "expires_in": 600,
                            "interval": 1,
                        },
                    )
                elif path == "/v1/auth/device/token":
                    if broker._deny:
                        self._json(400, {"error": "access_denied"})
                    elif broker._slow:
                        broker._slow = False
                        self._json(400, {"error": "slow_down", "interval": 1})
                    elif broker._pending > 0:
                        broker._pending -= 1
                        self._json(400, {"error": "authorization_pending", "interval": 1})
                    else:
                        self._json(200, broker._token())
                elif path == "/v1/auth/token":
                    broker.token_calls.append(dict(parse_qs(raw)))
                    self._json(200, broker._token())
                else:
                    self._json(404, {"error": "not_found"})

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", self.port), H)
        self._t = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.05})
        self._t.daemon = True

    def _token(self):
        return {
            "access_token": "cvcses_minted",
            "refresh_token": "cvcref_minted",
            "expires_in": 43200,
            "refresh_expires_in": 2592000,
            "principal": "joe",
            "role": "reader",
        }

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class TestPairing:
    def test_happy_path(self):
        with _StubBroker(pending_polls=1) as b:
            cred = oauth_native.pairing_login(
                b.url, timeout=10, printer=lambda *a: None, sleep=lambda *_: None
            )
            assert cred.access == "cvcses_minted"
            assert cred.refresh == "cvcref_minted"
            assert cred.server_url == b.url

    def test_slow_down_is_handled(self):
        with _StubBroker(pending_polls=0, slow_down_once=True) as b:
            cred = oauth_native.pairing_login(
                b.url, timeout=10, printer=lambda *a: None, sleep=lambda *_: None
            )
            assert cred.access == "cvcses_minted"

    def test_access_denied_raises(self):
        with _StubBroker(deny=True) as b:
            with pytest.raises(oauth_native.LoginError, match="access denied"):
                oauth_native.pairing_login(
                    b.url, timeout=10, printer=lambda *a: None, sleep=lambda *_: None
                )


class TestLoopback:
    def test_full_flow_and_no_verifier_in_url(self):
        captured = []

        with _StubBroker() as b:
            done = {}

            def run():
                try:
                    done["cred"] = oauth_native.loopback_login(
                        b.url, open_browser=False, timeout=10, printer=captured.append
                    )
                except Exception as exc:  # noqa: BLE001
                    done["err"] = exc

            t = threading.Thread(target=run)
            t.start()

            # Wait for the printed authorize URL, then act as the browser.
            authorize_url = _wait_for_url(captured)
            q = parse_qs(urlsplit(authorize_url).query)
            assert q["code_challenge_method"] == ["S256"]
            # The verifier must NEVER appear in the URL — only the challenge does.
            assert "code_verifier" not in q
            redirect = q["redirect_uri"][0]
            state = q["state"][0]
            urllib.request.urlopen(f"{redirect}?code=authcode&state={state}", timeout=5).read()

            t.join(timeout=10)
            assert "err" not in done, done.get("err")
            assert done["cred"].access == "cvcses_minted"
            # The exchange sent the code + a verifier that matches the challenge.
            sent = b.token_calls[-1]
            assert sent["grant_type"] == ["authorization_code"]
            assert _pkce_ok(sent["code_verifier"][0], q["code_challenge"][0])

    def test_state_mismatch_refused(self):
        captured = []
        with _StubBroker() as b:
            done = {}

            def run():
                try:
                    done["cred"] = oauth_native.loopback_login(
                        b.url, open_browser=False, timeout=10, printer=captured.append
                    )
                except Exception as exc:  # noqa: BLE001
                    done["err"] = exc

            t = threading.Thread(target=run)
            t.start()
            authorize_url = _wait_for_url(captured)
            redirect = parse_qs(urlsplit(authorize_url).query)["redirect_uri"][0]
            urllib.request.urlopen(f"{redirect}?code=x&state=WRONG", timeout=5).read()
            t.join(timeout=10)
            assert isinstance(done.get("err"), oauth_native.LoginError)


def _wait_for_url(captured, deadline=8.0):
    end = time.time() + deadline
    while time.time() < end:
        for line in captured:
            for tok in str(line).split():
                if tok.startswith("http") and "/v1/auth/authorize" in tok:
                    return tok
        time.sleep(0.05)
    raise AssertionError("authorize URL was never printed")
