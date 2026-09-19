# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Coverage tests for the login / logout / whoami / auth CLI commands
(cvcpkg.cli._auth).

Everything that would touch a network or the on-disk credential store is
mocked: ``cvcpkg.oauth_native`` (login flows / provider discovery),
``cvcpkg.credentials`` (save / get / token_for / logout_local), the module's
own ``_request`` helper, and ``urllib.request.urlopen`` for the direct
``_request`` unit tests.  No sockets, no files.
"""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.error
from unittest import mock

import click
import pytest
from click.testing import CliRunner

from cvcpkg import credentials
from cvcpkg import oauth_native
from cvcpkg.cli import _auth
from cvcpkg.cli import cli


SRV = "https://x.example"


def _cred(**over) -> credentials.Credential:
    base = dict(
        access="cvcses_abc",
        principal="joe",
        role="reader",
        device="laptop",
        expires_at="2026-12-31T00:00:00+00:00",
        server_url=SRV,
        session_id=7,
    )
    base.update(over)
    return credentials.Credential(**base)


# ── _server_default ─────────────────────────────────────────────


def test_server_default_delegates_to_config():
    with mock.patch("cvcpkg.config.default_server_url", return_value="https://d.example"):
        assert _auth._server_default() == "https://d.example"


# ── _request ────────────────────────────────────────────────────


class _AuthResp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_AuthResp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def test_request_success_with_body_and_headers():
    captured = {}

    def _fake(req, timeout=0):
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization")
        captured["ctype"] = req.get_header("Content-type")
        captured["data"] = req.data
        return _AuthResp(200, b'{"ok": true}')

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        status, data = _auth._request("POST", f"{SRV}/v1/x", token="tok", json_body={"a": 1})
    assert status == 200
    assert data == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer tok"
    assert captured["ctype"] == "application/json"
    assert json.loads(captured["data"]) == {"a": 1}


def test_request_success_empty_body_returns_none():
    with mock.patch("urllib.request.urlopen", return_value=_AuthResp(204, b"")):
        status, data = _auth._request("DELETE", f"{SRV}/v1/x", token="tok")
    assert status == 204
    assert data is None


def test_request_http_error_with_json_body():
    err = urllib.error.HTTPError(
        f"{SRV}/v1/x", 409, "Conflict", {}, io.BytesIO(b'{"error": "dup"}')
    )
    with mock.patch("urllib.request.urlopen", side_effect=err):
        status, data = _auth._request("POST", f"{SRV}/v1/x")
    assert status == 409
    assert data == {"error": "dup"}


def test_request_http_error_with_non_json_body():
    err = urllib.error.HTTPError(f"{SRV}/v1/x", 500, "Boom", {}, io.BytesIO(b"not json"))
    with mock.patch("urllib.request.urlopen", side_effect=err):
        status, data = _auth._request("GET", f"{SRV}/v1/x")
    assert status == 500
    assert data is None


def test_request_url_error_raises_clickexception():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        with pytest.raises(click.ClickException) as ei:
            _auth._request("GET", f"{SRV}/v1/x")
    assert "could not reach" in str(ei.value)


def test_request_timeout_raises_clickexception():
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
        with pytest.raises(click.ClickException):
            _auth._request("GET", f"{SRV}/v1/x")


# ── _prompt_provider ────────────────────────────────────────────


def test_prompt_provider_returns_selected_id():
    choices = [{"id": "ringa", "display_name": "Ring A"}, {"id": "ringb"}, {}]
    with mock.patch("click.prompt", return_value=2):
        chosen = _auth._prompt_provider(choices)
    assert chosen == "ringb"


# ── login ───────────────────────────────────────────────────────


def test_login_loopback_success_text():
    cred = _cred()
    with (
        mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[]),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch("cvcpkg.oauth_native.loopback_login", return_value=cred) as lb,
        mock.patch("cvcpkg.credentials.save") as save,
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV])
    assert res.exit_code == 0, res.output
    assert "Signed in to x.example as joe (reader)." in res.output
    assert "device:  laptop" in res.output
    assert "expires: 2026-12-31" in res.output
    lb.assert_called_once()
    save.assert_called_once()


def test_login_json_output():
    cred = _cred()
    with (
        mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[]),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch("cvcpkg.oauth_native.loopback_login", return_value=cred),
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV, "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["principal"] == "joe"
    assert payload["role"] == "reader"


def test_login_single_provider_autoselected():
    cred = _cred()
    with (
        mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[{"id": "ringa"}]),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch("cvcpkg.oauth_native.loopback_login", return_value=cred) as lb,
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV])
    assert res.exit_code == 0, res.output
    assert lb.call_args.kwargs["provider"] == "ringa"


def test_login_pairing_when_no_browser():
    cred = _cred()
    with (
        mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[]),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=False),
        mock.patch("cvcpkg.oauth_native.pairing_login", return_value=cred) as pl,
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV, "--no-browser"])
    assert res.exit_code == 0, res.output
    pl.assert_called_once()
    assert "Signed in to x.example" in res.output


def test_login_multi_provider_noninteractive_pairing_errors():
    # multiple providers, no --provider, not a tty, and not the loopback flow
    # (pairing) -> hard error asking for --provider.
    with (
        mock.patch(
            "cvcpkg.oauth_native.fetch_providers",
            return_value=[{"id": "ringa"}, {"id": "ringb"}],
        ),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=False),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV, "--no-browser"])
    assert res.exit_code != 0
    assert "multiple identity providers" in res.output
    assert "ringa" in res.output and "ringb" in res.output


def test_login_multi_provider_tty_prompts():
    cred = _cred()
    fake_sys = types.SimpleNamespace(
        stdin=types.SimpleNamespace(isatty=lambda: True), exit=sys.exit
    )
    with (
        mock.patch("cvcpkg.cli._auth.sys", fake_sys),
        mock.patch(
            "cvcpkg.oauth_native.fetch_providers",
            return_value=[{"id": "ringa"}, {"id": "ringb"}],
        ),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=False),
        mock.patch("cvcpkg.cli._auth._prompt_provider", return_value="ringb") as pp,
        mock.patch("cvcpkg.oauth_native.pairing_login", return_value=cred) as pl,
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV, "--no-browser"])
    assert res.exit_code == 0, res.output
    pp.assert_called_once()
    assert pl.call_args.kwargs["provider"] == "ringb"


def test_login_explicit_provider_validated_ok():
    cred = _cred()
    with (
        mock.patch(
            "cvcpkg.oauth_native.fetch_providers",
            return_value=[{"id": "ringa"}, {"id": "ringb"}],
        ),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch("cvcpkg.oauth_native.loopback_login", return_value=cred) as lb,
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV, "--provider", "ringb"])
    assert res.exit_code == 0, res.output
    assert lb.call_args.kwargs["provider"] == "ringb"


def test_login_explicit_provider_unknown_errors():
    with (
        mock.patch(
            "cvcpkg.oauth_native.fetch_providers",
            return_value=[{"id": "ringa"}, {"id": "ringb"}],
        ),
        mock.patch(
            "cvcpkg.oauth_native.loopback_login",
            side_effect=AssertionError("must not reach login flow"),
        ),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV, "--provider", "nope"])
    assert res.exit_code != 0
    assert "unknown provider 'nope'" in res.output
    assert "ringa" in res.output and "ringb" in res.output


def test_login_multi_provider_loopback_fallthrough():
    # multiple providers + loopback flow + not a tty: neither prompt nor error;
    # provider stays empty and the browser picker handles it downstream.
    cred = _cred()
    with (
        mock.patch(
            "cvcpkg.oauth_native.fetch_providers",
            return_value=[{"id": "ringa"}, {"id": "ringb"}],
        ),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch("cvcpkg.oauth_native.loopback_login", return_value=cred) as lb,
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV])
    assert res.exit_code == 0, res.output
    assert lb.call_args.kwargs["provider"] == ""


def test_login_success_minimal_cred_omits_device_and_expiry():
    cred = _cred(device="", expires_at="")
    with (
        mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[]),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch("cvcpkg.oauth_native.loopback_login", return_value=cred),
        mock.patch("cvcpkg.credentials.save"),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV])
    assert res.exit_code == 0, res.output
    assert "Signed in to x.example as joe (reader)." in res.output
    assert "device:" not in res.output
    assert "expires:" not in res.output


def test_login_loginerror_becomes_clickexception():
    with (
        mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[]),
        mock.patch("cvcpkg.oauth_native.can_open_browser", return_value=True),
        mock.patch(
            "cvcpkg.oauth_native.loopback_login",
            side_effect=oauth_native.LoginError("no dice"),
        ),
    ):
        res = CliRunner().invoke(cli, ["login", "--server", SRV])
    assert res.exit_code != 0
    assert "no dice" in res.output


# ── logout ──────────────────────────────────────────────────────


def test_logout_not_signed_in():
    with mock.patch("cvcpkg.credentials.get", return_value=None):
        res = CliRunner().invoke(cli, ["logout", "--server", SRV])
    assert res.exit_code == 0
    assert "Not signed in to x.example." in res.output


def test_logout_local_only_skips_server():
    with (
        mock.patch("cvcpkg.credentials.get", return_value=_cred()),
        mock.patch("cvcpkg.credentials.logout_local") as ll,
        mock.patch("cvcpkg.cli._auth._request", side_effect=AssertionError("no network")),
    ):
        res = CliRunner().invoke(cli, ["logout", "--server", SRV, "--local-only"])
    assert res.exit_code == 0
    assert "Signed out of x.example." in res.output
    ll.assert_called_once()


def test_logout_server_revoke_ok():
    with (
        mock.patch("cvcpkg.credentials.get", return_value=_cred()),
        mock.patch("cvcpkg.credentials.logout_local") as ll,
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, None)) as rq,
    ):
        res = CliRunner().invoke(cli, ["logout", "--server", SRV, "--all"])
    assert res.exit_code == 0
    assert "Signed out of x.example." in res.output
    ll.assert_called_once()
    # --all threaded into the revoke body
    assert rq.call_args.kwargs["json_body"] == {"all": True}


def test_logout_server_revoke_nonok_warns_but_clears():
    with (
        mock.patch("cvcpkg.credentials.get", return_value=_cred()),
        mock.patch("cvcpkg.credentials.logout_local") as ll,
        mock.patch("cvcpkg.cli._auth._request", return_value=(500, None)),
    ):
        res = CliRunner().invoke(cli, ["logout", "--server", SRV])
    assert res.exit_code == 0
    assert "server-side revoke returned 500" in res.output
    assert "Signed out of x.example." in res.output
    ll.assert_called_once()


# ── whoami ──────────────────────────────────────────────────────


def test_whoami_not_signed_in():
    with mock.patch("cvcpkg.cli._helpers.resolve_token", return_value=""):
        res = CliRunner().invoke(cli, ["whoami", "--server", SRV])
    assert res.exit_code != 0
    assert "not signed in" in res.output


def test_whoami_session_expired():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(401, None)),
    ):
        res = CliRunner().invoke(cli, ["whoami", "--server", SRV])
    assert res.exit_code != 0
    assert "session expired" in res.output


def test_whoami_generic_failure():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(503, None)),
    ):
        res = CliRunner().invoke(cli, ["whoami", "--server", SRV])
    assert res.exit_code != 0
    assert "whoami failed (503)" in res.output


def test_whoami_text_output():
    data = {
        "name": "Joe Rivera",
        "role": "publisher",
        "kind": "user",
        "email": "joe@x.example",
        "orgs": [{"slug": "acme", "role": "owner"}],
    }
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, data)),
    ):
        res = CliRunner().invoke(cli, ["whoami", "--server", SRV])
    assert res.exit_code == 0
    assert "Joe Rivera  (publisher, user)" in res.output
    assert "email: joe@x.example" in res.output
    assert "acme (owner)" in res.output


def test_whoami_text_no_email_no_orgs():
    data = {"name": "Joe", "role": "reader", "kind": "user"}
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, data)),
    ):
        res = CliRunner().invoke(cli, ["whoami", "--server", SRV])
    assert res.exit_code == 0
    assert "Joe  (reader, user)" in res.output
    assert "email:" not in res.output
    assert "orgs:" not in res.output


def test_whoami_json_output():
    data = {"name": "Joe", "role": "reader", "kind": "user"}
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, data)),
    ):
        res = CliRunner().invoke(cli, ["whoami", "--server", SRV, "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["name"] == "Joe"


# ── auth devices ────────────────────────────────────────────────


def test_auth_devices_not_signed_in():
    with mock.patch("cvcpkg.cli._helpers.resolve_token", return_value=""):
        res = CliRunner().invoke(cli, ["auth", "devices", "--server", SRV])
    assert res.exit_code != 0
    assert "not signed in" in res.output


def test_auth_devices_session_expired():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(401, None)),
    ):
        res = CliRunner().invoke(cli, ["auth", "devices", "--server", SRV])
    assert res.exit_code != 0
    assert "session expired" in res.output


def test_auth_devices_failure():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(500, None)),
    ):
        res = CliRunner().invoke(cli, ["auth", "devices", "--server", SRV])
    assert res.exit_code != 0
    assert "listing devices failed (500)" in res.output


def test_auth_devices_empty():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, {"devices": []})),
    ):
        res = CliRunner().invoke(cli, ["auth", "devices", "--server", SRV])
    assert res.exit_code == 0
    assert "No active sessions." in res.output


def test_auth_devices_text_lists_current():
    data = {
        "devices": [
            {
                "session_id": 1,
                "device_label": "laptop",
                "ip_at_issue": "10.0.0.1",
                "expires_at": "2026-12-31",
                "current": True,
            },
            {"session_id": 2, "expires_at": "2027-01-01"},
        ]
    }
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, data)),
    ):
        res = CliRunner().invoke(cli, ["auth", "devices", "--server", SRV])
    assert res.exit_code == 0
    assert "#1" in res.output
    assert "laptop" in res.output
    assert "(this session)" in res.output
    assert "(unnamed)" in res.output  # second device has no label


def test_auth_devices_json():
    data = {"devices": [{"session_id": 1}]}
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(200, data)),
    ):
        res = CliRunner().invoke(cli, ["auth", "devices", "--server", SRV, "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output) == [{"session_id": 1}]


# ── auth revoke ─────────────────────────────────────────────────


def test_auth_revoke_not_signed_in():
    with mock.patch("cvcpkg.cli._helpers.resolve_token", return_value=""):
        res = CliRunner().invoke(cli, ["auth", "revoke", "5", "--server", SRV])
    assert res.exit_code != 0
    assert "not signed in" in res.output


def test_auth_revoke_success():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(204, None)) as rq,
    ):
        res = CliRunner().invoke(cli, ["auth", "revoke", "5", "--server", SRV])
    assert res.exit_code == 0
    assert "Revoked session #5." in res.output
    assert rq.call_args.args[0] == "DELETE"


def test_auth_revoke_failure():
    with (
        mock.patch("cvcpkg.cli._helpers.resolve_token", return_value="tok"),
        mock.patch("cvcpkg.cli._auth._request", return_value=(404, None)),
    ):
        res = CliRunner().invoke(cli, ["auth", "revoke", "5", "--server", SRV])
    assert res.exit_code != 0
    assert "revoke failed (404)" in res.output


# ── auth status ─────────────────────────────────────────────────


def test_auth_status_not_signed_in():
    with (
        mock.patch("cvcpkg.credentials.host_of", return_value="x.example"),
        mock.patch("cvcpkg.credentials.token_for", return_value=""),
    ):
        res = CliRunner().invoke(cli, ["auth", "status", "--server", SRV])
    assert res.exit_code == 1
    assert "Not signed in to x.example." in res.output


def test_auth_status_signed_in():
    with (
        mock.patch("cvcpkg.credentials.host_of", return_value="x.example"),
        mock.patch("cvcpkg.credentials.token_for", return_value="tok"),
        mock.patch("cvcpkg.credentials.get", return_value=_cred()),
    ):
        res = CliRunner().invoke(cli, ["auth", "status", "--server", SRV])
    assert res.exit_code == 0
    assert "Signed in to x.example as joe (reader)." in res.output


# ── auth providers ──────────────────────────────────────────────


def test_auth_providers_none():
    with mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=[]):
        res = CliRunner().invoke(cli, ["auth", "providers", "--server", SRV])
    assert res.exit_code == 0
    assert "no OIDC providers configured" in res.output


def test_auth_providers_text():
    choices = [{"id": "ringa", "display_name": "Ring A"}, {"id": "ringb"}]
    with mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=choices):
        res = CliRunner().invoke(cli, ["auth", "providers", "--server", SRV])
    assert res.exit_code == 0
    assert "ringa" in res.output
    assert "Ring A" in res.output
    assert "ringb" in res.output


def test_auth_providers_json():
    choices = [{"id": "ringa"}]
    with mock.patch("cvcpkg.oauth_native.fetch_providers", return_value=choices):
        res = CliRunner().invoke(cli, ["auth", "providers", "--server", SRV, "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output) == choices
