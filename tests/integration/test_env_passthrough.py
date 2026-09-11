"""Environment variables reach the app through the *production* launch path.

The bug class this guards against is the silent dead knob: an env var that is
read somewhere in the codebase, documented in the compose file, set by an
operator — and then never actually consulted by the running server, so the
feature it controls looks configured and is not.
``CVCPKG_SERVER_REQUIRE_AUTH_READS`` was exactly this for weeks (#523), and the
Phase 13 OIDC login sat dormant in production for the same reason: the code was
deployed, the route was in the OpenAPI, and the four variables that switch it on
were never passed through ``docker-compose.production.yml``.

So these tests do not import ``create_app``.  They launch the server the way
production does — ``python -m uvicorn --factory cvcpkg.server.app:create_app``,
matching ``Dockerfile.production`` — with the variables in the environment, and
assert the behaviour changes.  Every new env var in the SSO work gets a row here.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest


def _text(raw: bytes | None) -> str:
    """Decode child-process output without dying on its console encoding.

    The server's own startup errors contain non-ASCII (an em-dash), and on
    Windows the child emits those in cp1252 — a strict utf-8 decode then raises
    UnicodeDecodeError from inside the error path, hiding the failure it was
    trying to report.
    """
    return (raw or b"").decode("utf-8", errors="replace")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _StubIdP(threading.Thread):
    """A minimal OIDC discovery endpoint, so the server has a real issuer."""

    daemon = True

    def __init__(self) -> None:
        super().__init__()
        self.port = _free_port()
        issuer = f"http://127.0.0.1:{self.port}"
        doc = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/auth",
            "token_endpoint": f"{issuer}/token",
            "userinfo_endpoint": f"{issuer}/userinfo",
        }
        body = json.dumps(doc).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.startswith("/.well-known/openid-configuration"):
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def log_message(self, *a):  # keep pytest output clean
                pass

        self._srv = HTTPServer(("127.0.0.1", self.port), Handler)
        self.issuer = issuer

    def run(self) -> None:
        self._srv.serve_forever(poll_interval=0.1)

    def stop(self) -> None:
        self._srv.shutdown()


class _Server:
    """cvcpkg-server launched exactly as the production image launches it."""

    def __init__(self, tmp_path, env: dict[str, str]) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._env = {
            **os.environ,
            "CVCPKG_SERVER_STATE_DIR": str(tmp_path),
            # A Secure cookie is never returned over plain http.
            "CVCPKG_COOKIE_SECURE": "0",
            **env,
        }
        self._env.pop("CVCPKG_MIRROR_MODE", None)
        self._proc = None

    def __enter__(self) -> _Server:
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--factory",
                "cvcpkg.server.app:create_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "error",
            ],
            env=self._env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self._proc.poll() is not None:
                err = _text(self._proc.stderr.read())[-2000:]
                raise RuntimeError(f"server exited: {err}")
            try:
                if httpx.get(f"{self.url}/healthz", timeout=2).status_code == 200:
                    return self
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("server did not become ready")

    def __exit__(self, *exc) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def wait_for_exit(self, timeout: float = 30.0) -> tuple[int, str]:
        assert self._proc is not None
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            raise
        return self._proc.returncode, _text(self._proc.stderr.read())


@pytest.fixture(scope="module")
def idp():
    stub = _StubIdP()
    stub.start()
    yield stub
    stub.stop()


def test_oidc_dormant_without_configuration(tmp_path):
    """The state cvcpkg.org was actually in: route present, feature off."""
    with _Server(tmp_path, {}) as srv:
        assert httpx.get(f"{srv.url}/admin/oidc/login", follow_redirects=False).status_code == 404
        # ...and the route really is registered, so 404 means "not configured",
        # not "this build lacks the feature".
        spec = httpx.get(f"{srv.url}/openapi.json", timeout=10).json()
        assert "/admin/oidc/login" in spec["paths"]


def test_oidc_env_vars_reach_the_running_server(tmp_path, idp):
    """The regression gate: set the four variables, get a redirect to the IdP."""
    env = {
        "CVCPKG_OIDC_ISSUER": idp.issuer,
        "CVCPKG_OIDC_CLIENT_ID": "cvcpkg-org",
        "CVCPKG_OIDC_CLIENT_SECRET": "shhh",
        "CVCPKG_OIDC_REDIRECT_URL": "http://127.0.0.1/admin/oidc/callback",
        "CVCPKG_OIDC_ADMIN_GROUPS": "cvcpkg-admin",
    }
    with _Server(tmp_path, env) as srv:
        r = httpx.get(f"{srv.url}/admin/oidc/login", follow_redirects=False)
        assert r.status_code == 303, r.text
        loc = r.headers["location"]
        assert loc.startswith(f"{idp.issuer}/auth?")
        assert "code_challenge_method=S256" in loc
        assert "client_id=cvcpkg-org" in loc
        assert "nonce=" in loc
        # The login page offers the button rather than only a token field.
        assert "/admin/oidc/login" in httpx.get(f"{srv.url}/admin").text


def test_partial_oidc_config_stays_dormant(tmp_path, idp):
    """Three of four set is not 'nearly working' — it must not half-enable."""
    env = {
        "CVCPKG_OIDC_ISSUER": idp.issuer,
        "CVCPKG_OIDC_CLIENT_ID": "cvcpkg-org",
        "CVCPKG_OIDC_CLIENT_SECRET": "shhh",
        # CVCPKG_OIDC_REDIRECT_URL deliberately absent
    }
    with _Server(tmp_path, env) as srv:
        assert httpx.get(f"{srv.url}/admin/oidc/login", follow_redirects=False).status_code == 404


def test_universal_group_map_refuses_to_boot(tmp_path, idp):
    """A group every account holds must fail at deploy time, not at login."""
    env = {
        "CVCPKG_OIDC_ISSUER": idp.issuer,
        "CVCPKG_OIDC_CLIENT_ID": "cvcpkg-org",
        "CVCPKG_OIDC_CLIENT_SECRET": "shhh",
        "CVCPKG_OIDC_REDIRECT_URL": "http://127.0.0.1/admin/oidc/callback",
        "CVCPKG_OIDC_ADMIN_GROUPS": "user",
    }
    srv = _Server(tmp_path, env)
    with pytest.raises(RuntimeError, match="server exited"):
        with srv:
            pass


def test_cookie_secure_env_var_is_honoured(tmp_path, idp):
    env = {
        "CVCPKG_OIDC_ISSUER": idp.issuer,
        "CVCPKG_OIDC_CLIENT_ID": "cvcpkg-org",
        "CVCPKG_OIDC_CLIENT_SECRET": "shhh",
        "CVCPKG_OIDC_REDIRECT_URL": "http://127.0.0.1/admin/oidc/callback",
        "CVCPKG_COOKIE_SECURE": "1",
    }
    with _Server(tmp_path, env) as srv:
        r = httpx.get(f"{srv.url}/admin/oidc/login", follow_redirects=False)
        assert "secure" in r.headers["set-cookie"].lower()
