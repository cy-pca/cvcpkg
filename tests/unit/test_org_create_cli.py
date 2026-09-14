# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""`cvcpkg org create` and the org group's session-token fallback."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from cvcpkg.cli import _server, cli


@pytest.fixture()
def runner(monkeypatch):
    # No ambient token or credential file, so resolution is deterministic.
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    monkeypatch.setenv("CVCPKG_CREDENTIALS_FILE", "/nonexistent-credentials-xyz.yaml")
    return CliRunner()


def test_create_posts_expected_body_with_explicit_token(runner, monkeypatch):
    calls: dict = {}

    def fake(method, url, token, **kw):
        calls.update(method=method, url=url, token=token, kw=kw)
        return {"slug": "acme", "is_private": True}

    monkeypatch.setattr(_server, "_api_request", fake)
    res = runner.invoke(
        cli,
        [
            "org",
            "create",
            "acme",
            "--server",
            "https://s.example",
            "--token",
            "cvctok_x",
            "--display-name",
            "Acme",
            "--private",
        ],
    )
    assert res.exit_code == 0, res.output
    assert calls["method"] == "post"
    assert calls["url"].endswith("/v1/orgs")
    assert calls["token"] == "cvctok_x"
    body = calls["kw"]["json"]
    assert body["slug"] == "acme"
    assert body["display_name"] == "Acme"
    assert body["is_private"] is True
    assert "private organization 'acme'" in res.output


def test_create_falls_back_to_login_session(runner, monkeypatch):
    captured: dict = {}

    def fake(method, url, token, **kw):
        captured["token"] = token
        return {"slug": "acme"}

    monkeypatch.setattr(_server, "_api_request", fake)
    monkeypatch.setattr("cvcpkg.credentials.token_for", lambda host: "cvcses_sess")
    res = runner.invoke(cli, ["org", "create", "acme", "--server", "https://s.example"])
    assert res.exit_code == 0, res.output
    assert captured["token"] == "cvcses_sess"
    assert "public organization 'acme'" in res.output


def test_create_errors_without_any_credential(runner, monkeypatch):
    monkeypatch.setattr("cvcpkg.credentials.token_for", lambda host: "")
    res = runner.invoke(cli, ["org", "create", "acme", "--server", "https://s.example"])
    assert res.exit_code != 0
    assert "not authenticated" in res.output


def test_add_member_also_uses_session(runner, monkeypatch):
    captured: dict = {}

    def fake(method, url, token, **kw):
        captured["token"] = token
        return {}

    monkeypatch.setattr(_server, "_api_request", fake)
    monkeypatch.setattr("cvcpkg.credentials.token_for", lambda host: "cvcses_sess")
    res = runner.invoke(
        cli, ["org", "add-member", "acme", "--user", "alice", "--server", "https://s.example"]
    )
    assert res.exit_code == 0, res.output
    assert captured["token"] == "cvcses_sess"
