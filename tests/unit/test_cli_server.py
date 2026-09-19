# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for the client->server API CLI commands in ``cvcpkg.cli._server``.

Covers the ``token``/``user``/``org`` groups, ``register``, and the
``server`` management commands.  Every HTTP call is mocked: the
``_api_request`` helper is patched for commands that go through it, and
``httpx.Client`` is faked for the commands that call it directly, so no
network, server, or credentials are ever needed.
"""

from __future__ import annotations

from unittest import mock

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli import _server, cli
from cvcpkg.cli._server import _human_bytes, _resolve_member


@pytest.fixture()
def runner(monkeypatch):
    # No ambient token or credential file, so token resolution is deterministic.
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    monkeypatch.setenv("CVCPKG_CREDENTIALS_FILE", "/nonexistent-credentials-xyz.yaml")
    return CliRunner()


# ── httpx fakes ─────────────────────────────────────────────────


def _resp(status=200, json_data=None, text="", json_error=False):
    r = mock.MagicMock()
    r.status_code = status
    r.text = text
    if json_error:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = {} if json_data is None else json_data
    return r


def _client(**methods):
    """A fake httpx.Client context manager; kwargs set method return values."""
    c = mock.MagicMock()
    c.__enter__ = mock.MagicMock(return_value=c)
    c.__exit__ = mock.MagicMock(return_value=False)
    for name, resp in methods.items():
        getattr(c, name).return_value = resp
    return c


def _patch_httpx(monkeypatch, client):
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)


def _patch_api(monkeypatch, ret=None, side_effect=None):
    """Patch ``_server._api_request`` and capture its calls."""
    calls: list[dict] = []

    def fake(method, url, token, **kw):
        calls.append({"method": method, "url": url, "token": token, "kw": kw})
        if side_effect is not None:
            raise side_effect
        return {} if ret is None else ret

    monkeypatch.setattr(_server, "_api_request", fake)
    return calls


# ── _api_request itself ─────────────────────────────────────────


class TestApiRequest:
    def test_returns_parsed_json_on_success(self, monkeypatch):
        client = _client(get=_resp(200, {"ok": 1}))
        _patch_httpx(monkeypatch, client)
        assert _server._api_request("get", "http://x/y", "tok") == {"ok": 1}
        # The bearer token is sent as an Authorization header.
        _, kwargs = client.get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_error_uses_json_detail(self, monkeypatch):
        client = _client(post=_resp(400, {"detail": "bad name"}, text="raw"))
        _patch_httpx(monkeypatch, client)
        with pytest.raises(Exception) as ei:
            _server._api_request("post", "http://x/y", "tok", json={})
        assert "server returned 400: bad name" in str(ei.value)

    def test_error_falls_back_to_text_when_not_json(self, monkeypatch):
        client = _client(delete=_resp(500, json_error=True, text="boom"))
        _patch_httpx(monkeypatch, client)
        with pytest.raises(Exception) as ei:
            _server._api_request("delete", "http://x/y", "tok")
        assert "server returned 500: boom" in str(ei.value)


# ── token group ─────────────────────────────────────────────────


class TestTokenCreate:
    def test_create_with_expiry(self, runner, monkeypatch):
        calls = _patch_api(
            monkeypatch,
            {"name": "ci", "role": "reader", "token": "cvctok_new", "expires_at": "2027-01-01"},
        )
        res = runner.invoke(
            cli,
            ["token", "create", "--server", "http://s", "--token", "adm",
             "--name", "ci", "--role", "reader", "--expires-in-days", "30"],
        )
        assert res.exit_code == 0, res.output
        assert "Created token 'ci' (role: reader)" in res.output
        assert "cvctok_new" in res.output
        assert "Expires: 2027-01-01" in res.output
        body = calls[0]["kw"]["json"]
        assert body == {"name": "ci", "role": "reader", "expires_in_days": 30}

    def test_create_without_expiry_omits_field(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch, {"name": "ci", "role": "admin", "token": "t"})
        res = runner.invoke(
            cli,
            ["token", "create", "--server", "http://s", "--token", "adm",
             "--name", "ci", "--role", "admin"],
        )
        assert res.exit_code == 0, res.output
        assert "expires_in_days" not in calls[0]["kw"]["json"]
        assert "Expires:" not in res.output


class TestTokenList:
    def test_empty(self, runner, monkeypatch):
        _patch_api(monkeypatch, {"tokens": []})
        res = runner.invoke(cli, ["token", "list", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "No tokens found." in res.output

    def test_lists_tokens_with_status(self, runner, monkeypatch):
        _patch_api(
            monkeypatch,
            {"tokens": [
                {"name": "alice", "role": "publisher", "expires_at": "2027-02-02"},
                {"name": "bob", "role": "reader", "revoked": True},
            ]},
        )
        res = runner.invoke(cli, ["token", "list", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "alice" in res.output and "expires=2027-02-02" in res.output
        assert "[REVOKED]" in res.output


class TestTokenRevoke:
    def test_revoke(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        res = runner.invoke(
            cli, ["token", "revoke", "--server", "http://s", "--token", "adm", "--name", "old"]
        )
        assert res.exit_code == 0, res.output
        assert "Revoked token 'old'." in res.output
        assert calls[0]["method"] == "delete"
        assert calls[0]["url"].endswith("/v1/tokens/old")


class TestTokenRotate:
    def test_rotate_with_grace(self, runner, monkeypatch):
        calls = _patch_api(
            monkeypatch,
            {"name": "svc", "role": "publisher", "token": "cvctok_r",
             "previous_valid_until": "2026-10-01", "expires_at": "2027-01-01"},
        )
        res = runner.invoke(
            cli,
            ["token", "rotate", "--server", "http://s", "--token", "adm",
             "--name", "svc", "--grace-minutes", "60"],
        )
        assert res.exit_code == 0, res.output
        assert "Rotated token 'svc'" in res.output
        assert "New token: cvctok_r" in res.output
        assert "Old secret valid until: 2026-10-01" in res.output
        assert "Expires: 2027-01-01" in res.output
        assert calls[0]["kw"]["json"] == {"grace_minutes": 60}

    def test_rotate_without_grace_reports_immediate(self, runner, monkeypatch):
        _patch_api(monkeypatch, {"name": "svc", "role": "reader", "token": "t"})
        res = runner.invoke(
            cli, ["token", "rotate", "--server", "http://s", "--token", "adm", "--name", "svc"]
        )
        assert res.exit_code == 0, res.output
        assert "Old secret is no longer valid." in res.output


class TestTokenProfileEdits:
    def test_set_email(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        res = runner.invoke(
            cli,
            ["token", "set-email", "--server", "http://s", "--token", "adm",
             "--name", "svc", "--email", "a@b.c"],
        )
        assert res.exit_code == 0, res.output
        assert "Email for 'svc' updated to 'a@b.c'." in res.output
        assert calls[0]["kw"]["json"] == {"email": "a@b.c"}

    def test_set_description(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        res = runner.invoke(
            cli,
            ["token", "set-description", "--server", "http://s", "--token", "adm",
             "--name", "svc", "--description", "hi there"],
        )
        assert res.exit_code == 0, res.output
        assert "Description for 'svc' updated." in res.output
        assert calls[0]["kw"]["json"] == {"description": "hi there"}

    def test_set_metadata(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        res = runner.invoke(
            cli,
            ["token", "set-metadata", "--server", "http://s", "--token", "adm",
             "--name", "svc", "--metadata", '{"k":1}'],
        )
        assert res.exit_code == 0, res.output
        assert "Metadata for 'svc' updated." in res.output
        assert calls[0]["kw"]["json"] == {"metadata": '{"k":1}'}


class TestTokenRequests:
    def test_empty(self, runner, monkeypatch):
        _patch_api(monkeypatch, {"requests": []})
        res = runner.invoke(cli, ["token", "requests", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "No token requests found." in res.output

    def test_lists_and_filters_by_status(self, runner, monkeypatch):
        calls = _patch_api(
            monkeypatch,
            {"requests": [
                {"id": 1, "name": "eve", "email": "e@x", "role": "reader", "status": "pending"},
            ]},
        )
        res = runner.invoke(
            cli,
            ["token", "requests", "--server", "http://s", "--token", "adm", "--status", "pending"],
        )
        assert res.exit_code == 0, res.output
        assert "eve" in res.output and "pending" in res.output
        assert calls[0]["kw"]["params"] == {"status": "pending"}


class TestTokenApproveDeny:
    def test_approve_with_token(self, runner, monkeypatch):
        _patch_api(monkeypatch, {"message": "approved", "token": "cvctok_a"})
        res = runner.invoke(cli, ["token", "approve", "5", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "approved" in res.output
        assert "Token: cvctok_a" in res.output

    def test_approve_without_token_key(self, runner, monkeypatch):
        _patch_api(monkeypatch, {"message": "approved (no token to hand out)"})
        res = runner.invoke(cli, ["token", "approve", "6", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "Token:" not in res.output

    def test_deny(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch, {"message": "denied"})
        res = runner.invoke(cli, ["token", "deny", "7", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "denied" in res.output
        assert calls[0]["url"].endswith("/v1/token-requests/7/deny")


# ── user group (direct httpx) ───────────────────────────────────


class TestUserInfo:
    def test_success(self, runner, monkeypatch):
        data = {"name": "alice", "role": "publisher", "email": "a@x",
                "description": "dev", "metadata": "m", "packages_published": 3,
                "created_at": "2026-01-01"}
        _patch_httpx(monkeypatch, _client(get=_resp(200, data)))
        res = runner.invoke(cli, ["user", "info", "alice", "--server", "http://s"])
        assert res.exit_code == 0, res.output
        assert "Name:        alice" in res.output
        assert "Packages:    3" in res.output
        assert "Metadata:    m" in res.output

    def test_not_found(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(404)))
        res = runner.invoke(cli, ["user", "info", "ghost", "--server", "http://s"])
        assert res.exit_code != 0
        assert "user 'ghost' not found" in res.output

    def test_server_error_with_detail(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(500, {"detail": "kaboom"})))
        res = runner.invoke(cli, ["user", "info", "alice", "--server", "http://s"])
        assert res.exit_code != 0
        assert "server returned 500: kaboom" in res.output

    def test_error_falls_back_to_text(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(500, json_error=True, text="raw body")))
        res = runner.invoke(cli, ["user", "info", "alice", "--server", "http://s"])
        assert res.exit_code != 0
        assert "server returned 500: raw body" in res.output

    def test_success_without_metadata_omits_line(self, runner, monkeypatch):
        data = {"name": "alice", "role": "reader", "email": "a@x", "packages_published": 0}
        _patch_httpx(monkeypatch, _client(get=_resp(200, data)))
        res = runner.invoke(cli, ["user", "info", "alice", "--server", "http://s"])
        assert res.exit_code == 0, res.output
        assert "Metadata:" not in res.output


class TestUserList:
    def test_empty(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(200, {"users": [], "total": 0})))
        res = runner.invoke(cli, ["user", "list", "--server", "http://s"])
        assert res.exit_code == 0, res.output
        assert "No users found." in res.output

    def test_populated_with_filters(self, runner, monkeypatch):
        client = _client(
            get=_resp(200, {
                "total": 1,
                "users": [{"name": "alice", "role": "admin", "email": "a@x",
                           "packages_published": 9}],
            })
        )
        _patch_httpx(monkeypatch, client)
        res = runner.invoke(
            cli,
            ["user", "list", "--server", "http://s", "--name", "al", "--email", "@x",
             "--role", "admin", "--org", "acme", "--has-published",
             "--sort", "packages_published", "--order", "desc",
             "--limit", "10", "--offset", "5"],
        )
        assert res.exit_code == 0, res.output
        assert "Showing 1 of 1 users:" in res.output
        assert "alice" in res.output
        params = client.get.call_args.kwargs["params"]
        assert params["name"] == "al"
        assert params["role"] == "admin"
        assert params["org"] == "acme"
        assert params["has_published"] == "true"
        assert params["limit"] == 10 and params["offset"] == 5

    def test_server_error(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(503, json_error=True, text="down")))
        res = runner.invoke(cli, ["user", "list", "--server", "http://s"])
        assert res.exit_code != 0
        assert "server returned 503: down" in res.output


class TestUserByEmail:
    def test_success_with_metadata(self, runner, monkeypatch):
        data = {"name": "bob", "role": "reader", "email": "b@x", "metadata": "m",
                "packages_published": 0}
        _patch_httpx(monkeypatch, _client(get=_resp(200, data)))
        res = runner.invoke(cli, ["user", "by-email", "b@x", "--server", "http://s"])
        assert res.exit_code == 0, res.output
        assert "Name:        bob" in res.output
        assert "Metadata:    m" in res.output

    def test_not_found(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(404)))
        res = runner.invoke(cli, ["user", "by-email", "no@x", "--server", "http://s"])
        assert res.exit_code != 0
        assert "no user with email 'no@x' found" in res.output

    def test_error_with_detail(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(500, {"detail": "oops"})))
        res = runner.invoke(cli, ["user", "by-email", "b@x", "--server", "http://s"])
        assert res.exit_code != 0
        assert "server returned 500: oops" in res.output

    def test_error_falls_back_to_text(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(502, json_error=True, text="bad gw")))
        res = runner.invoke(cli, ["user", "by-email", "b@x", "--server", "http://s"])
        assert res.exit_code != 0
        assert "server returned 502: bad gw" in res.output


# ── register (direct httpx) ─────────────────────────────────────


class TestRegister:
    def test_open_mode_returns_token(self, runner, monkeypatch):
        client = _client(post=_resp(200, {"message": "registered", "token": "cvctok_reg"}))
        _patch_httpx(monkeypatch, client)
        res = runner.invoke(
            cli,
            ["register", "--server", "http://s", "--name", "newbie", "--email", "n@x",
             "--role", "publisher", "--description", "desc", "--metadata", "meta"],
        )
        assert res.exit_code == 0, res.output
        assert "registered" in res.output
        assert "Token: cvctok_reg" in res.output
        assert "cvcpkg config set token cvctok_reg" in res.output
        body = client.post.call_args.kwargs["json"]
        assert body["description"] == "desc" and body["metadata"] == "meta"

    def test_admin_gated_returns_request_id(self, runner, monkeypatch):
        client = _client(post=_resp(200, {"message": "queued", "request_id": 42}))
        _patch_httpx(monkeypatch, client)
        res = runner.invoke(
            cli, ["register", "--server", "http://s", "--name", "n", "--email", "n@x"]
        )
        assert res.exit_code == 0, res.output
        assert "Request ID: 42" in res.output
        # Optional fields omitted when blank.
        body = client.post.call_args.kwargs["json"]
        assert "description" not in body and "metadata" not in body

    def test_error(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(409, {"detail": "name taken"})))
        res = runner.invoke(
            cli, ["register", "--server", "http://s", "--name", "dup", "--email", "d@x"]
        )
        assert res.exit_code != 0
        assert "server returned 409: name taken" in res.output

    def test_error_falls_back_to_text(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(500, json_error=True, text="splat")))
        res = runner.invoke(
            cli, ["register", "--server", "http://s", "--name", "n", "--email", "n@x"]
        )
        assert res.exit_code != 0
        assert "server returned 500: splat" in res.output


# ── server management ───────────────────────────────────────────


class TestServerStop:
    def test_confirmed_shutdown(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(200)))
        res = runner.invoke(
            cli, ["server", "stop", "--server", "http://s", "--token", "adm"], input="y\n"
        )
        assert res.exit_code == 0, res.output
        assert "Server shutdown initiated." in res.output

    def test_declining_aborts(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(200)))
        res = runner.invoke(
            cli, ["server", "stop", "--server", "http://s", "--token", "adm"], input="n\n"
        )
        assert res.exit_code != 0
        assert "Server shutdown initiated." not in res.output

    def test_forbidden(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(403)))
        res = runner.invoke(
            cli, ["server", "stop", "--server", "http://s", "--token", "adm", "--yes"]
        )
        assert res.exit_code != 0
        assert "admin token required" in res.output

    def test_other_error(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(500, {"detail": "internal"})))
        res = runner.invoke(
            cli, ["server", "stop", "--server", "http://s", "--token", "adm", "--yes"]
        )
        assert res.exit_code != 0
        assert "server returned 500: internal" in res.output

    def test_error_falls_back_to_text(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(post=_resp(502, json_error=True, text="gw down")))
        res = runner.invoke(
            cli, ["server", "stop", "--server", "http://s", "--token", "adm", "--yes"]
        )
        assert res.exit_code != 0
        assert "server returned 502: gw down" in res.output


class TestServerStatus:
    def test_ok(self, runner, monkeypatch):
        data = {"status": "ok", "version": "2.1.0", "packages_count": 5,
                "uptime_seconds": 60, "mirror_mode": True}
        _patch_httpx(monkeypatch, _client(get=_resp(200, data)))
        res = runner.invoke(cli, ["server", "status", "--server", "http://s"])
        assert res.exit_code == 0, res.output
        assert "Version:    2.1.0" in res.output
        assert "Mirror:     True" in res.output

    def test_non_200(self, runner, monkeypatch):
        _patch_httpx(monkeypatch, _client(get=_resp(503)))
        res = runner.invoke(cli, ["server", "status", "--server", "http://s"])
        assert res.exit_code != 0
        assert "server returned 503" in res.output

    def test_connect_error(self, runner, monkeypatch):
        client = _client()
        client.get.side_effect = httpx.ConnectError("refused")
        _patch_httpx(monkeypatch, client)
        res = runner.invoke(cli, ["server", "status", "--server", "http://s"])
        assert res.exit_code != 0
        assert "cannot connect to http://s" in res.output


class TestServerStatsAndBackup:
    def test_stats_full_report(self, runner, monkeypatch):
        _patch_api(
            monkeypatch,
            {"version": "2.0.0", "uptime_seconds": 12, "storage_scheme": "file",
             "mirror_mode": False, "database_backend": "postgresql", "packages_count": 42,
             "total_storage_bytes": 2048, "orgs_count": 3, "builders_count": 2,
             "builders_connected": 1, "build_jobs_count": 7, "audit_entries": 99},
        )
        res = runner.invoke(cli, ["server", "stats", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "postgresql" in res.output
        assert "Package storage:    2.0 KiB" in res.output
        assert "2 (1 connected)" in res.output

    def test_stats_minimal_uses_database_enabled_fallback(self, runner, monkeypatch):
        # No database_backend key: falls back to the enabled/n-a wording, and
        # the optional storage/orgs/builders lines are skipped.
        _patch_api(monkeypatch, {"version": "2.0.0", "database_enabled": False})
        res = runner.invoke(cli, ["server", "stats", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "Database:           n/a" in res.output
        assert "Package storage:" not in res.output

    def test_backup(self, runner, monkeypatch):
        _patch_api(
            monkeypatch,
            {"backend": "sqlite", "path": "/srv/backup.sqlite", "size_bytes": 4096},
        )
        res = runner.invoke(cli, ["server", "backup", "--server", "http://s", "--token", "adm"])
        assert res.exit_code == 0, res.output
        assert "Backup complete." in res.output
        assert "4.0 KiB" in res.output


# ── org group ───────────────────────────────────────────────────


class TestOrgMembers:
    def test_empty(self, runner, monkeypatch):
        _patch_api(monkeypatch, {"members": []})
        res = runner.invoke(
            cli, ["org", "members", "acme", "--server", "http://s", "--token", "adm"]
        )
        assert res.exit_code == 0, res.output
        assert "has no members" in res.output

    def test_populated(self, runner, monkeypatch):
        _patch_api(
            monkeypatch,
            {"members": [{"token_name": "alice", "role": "owner", "kind": "user"}]},
        )
        res = runner.invoke(
            cli, ["org", "members", "acme", "--server", "http://s", "--token", "adm"]
        )
        assert res.exit_code == 0, res.output
        assert "Members of 'acme':" in res.output
        assert "alice" in res.output and "owner" in res.output


class TestOrgAddRemove:
    def test_add_member_by_user(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        res = runner.invoke(
            cli,
            ["org", "add-member", "acme", "--user", "alice", "--role", "owner",
             "--server", "http://s", "--token", "adm"],
        )
        assert res.exit_code == 0, res.output
        assert "Added 'alice' to 'acme' as owner." in res.output
        assert calls[0]["kw"]["params"] == {
            "token_name": "alice", "role": "owner", "principal_kind": "user"
        }

    def test_add_member_uses_login_session(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        monkeypatch.setattr("cvcpkg.credentials.token_for", lambda host: "cvcses_sess")
        res = runner.invoke(
            cli,
            ["org", "add-member", "acme", "--token-name", "svc", "--server", "http://s"],
        )
        assert res.exit_code == 0, res.output
        assert calls[0]["token"] == "cvcses_sess"
        assert calls[0]["kw"]["params"]["principal_kind"] == "token"

    def test_add_member_requires_exactly_one_selector(self, runner, monkeypatch):
        _patch_api(monkeypatch)
        res = runner.invoke(
            cli,
            ["org", "add-member", "acme", "--user", "a", "--token-name", "b",
             "--server", "http://s", "--token", "adm"],
        )
        assert res.exit_code != 0
        assert "exactly one of --user, --token-name or --name" in res.output

    def test_add_member_without_credential_errors(self, runner, monkeypatch):
        monkeypatch.setattr("cvcpkg.credentials.token_for", lambda host: "")
        res = runner.invoke(
            cli, ["org", "add-member", "acme", "--user", "a", "--server", "http://s"]
        )
        assert res.exit_code != 0
        assert "not authenticated" in res.output

    def test_remove_member(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch)
        res = runner.invoke(
            cli,
            ["org", "remove-member", "acme", "--token-name", "svc",
             "--server", "http://s", "--token", "adm"],
        )
        assert res.exit_code == 0, res.output
        assert "Removed 'svc' from 'acme'." in res.output
        assert calls[0]["method"] == "delete"
        assert calls[0]["url"].endswith("/v1/orgs/acme/members/svc")


class TestOrgCreate:
    def test_public(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch, {"slug": "acme", "is_private": False})
        res = runner.invoke(
            cli,
            ["org", "create", "acme", "--server", "http://s", "--token", "adm",
             "--description", "d", "--homepage", "https://h"],
        )
        assert res.exit_code == 0, res.output
        assert "Created public organization 'acme'" in res.output
        body = calls[0]["kw"]["json"]
        assert body["display_name"] == "acme"  # defaults to slug
        assert body["is_private"] is False

    def test_private_with_display_name(self, runner, monkeypatch):
        calls = _patch_api(monkeypatch, {"slug": "acme", "is_private": True})
        res = runner.invoke(
            cli,
            ["org", "create", "acme", "--server", "http://s", "--token", "adm",
             "--display-name", "Acme Inc", "--private"],
        )
        assert res.exit_code == 0, res.output
        assert "Created private organization 'acme'" in res.output
        assert calls[0]["kw"]["json"]["display_name"] == "Acme Inc"


# ── small helpers ───────────────────────────────────────────────


class TestResolveMember:
    def test_exactly_one(self):
        assert _resolve_member("alice", "", "") == ("alice", "user")
        assert _resolve_member("", "svc", "") == ("svc", "token")
        assert _resolve_member("", "", "legacy") == ("legacy", "auto")

    def test_none_is_usage_error(self):
        import click

        with pytest.raises(click.UsageError):
            _resolve_member("", "", "")

    def test_two_is_usage_error(self):
        import click

        with pytest.raises(click.UsageError):
            _resolve_member("alice", "svc", "")


class TestHumanBytes:
    def test_zero_and_scales(self):
        assert _human_bytes(0) == "0 B"
        assert _human_bytes(1024) == "1.0 KiB"
        assert _human_bytes(1536) == "1.5 KiB"

    def test_terabytes(self):
        assert _human_bytes(5 * 1024**4) == "5.0 TiB"

    def test_non_numeric_passes_through(self):
        assert _human_bytes(None) == "None"
        assert _human_bytes("n/a") == "n/a"
