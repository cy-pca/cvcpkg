# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""`cvcpkg login --provider` validates the id client-side (fast-fail).

In the loopback flow the server's 400 for an unknown provider lands in the
browser, not the CLI, so without an up-front check a typo would hang until the
timeout and then report a misleading "timed out". These pin that a bad
``--provider`` fails immediately, and that an older server (which lists no
providers) still degrades gracefully.
"""

from __future__ import annotations

from click.testing import CliRunner

from cvcpkg import oauth_native
from cvcpkg.cli import cli


def test_login_without_role_is_accepted(monkeypatch):
    # Regression: --role is a click.Choice; an empty-string default trips click's
    # default validation ("'' is not one of ...") so plain `cvcpkg login` exited 2
    # before doing anything. It now defaults to None (no role requested).
    monkeypatch.setattr(oauth_native, "fetch_providers", lambda server, **kw: [])
    monkeypatch.setattr(oauth_native, "can_open_browser", lambda: True)
    seen = {}

    def _fake_loopback(server, **kw):
        seen["role"] = kw.get("role")
        raise oauth_native.LoginError("stub")

    monkeypatch.setattr(oauth_native, "loopback_login", _fake_loopback)

    res = CliRunner().invoke(cli, ["login", "--server", "https://x.example"])
    # Reached the flow (role normalized to "") instead of a click usage error.
    assert seen.get("role") == ""
    assert "is not one of" not in res.output


def test_bad_provider_fails_fast(monkeypatch):
    monkeypatch.setattr(
        oauth_native,
        "fetch_providers",
        lambda server, **kw: [{"id": "ringb", "display_name": "Ring B"}, {"id": "ringc"}],
    )
    # Should never reach the browser/loopback machinery.
    monkeypatch.setattr(
        oauth_native,
        "loopback_login",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    res = CliRunner().invoke(cli, ["login", "--server", "https://x.example", "--provider", "bogus"])
    assert res.exit_code != 0
    assert "unknown provider" in res.output
    assert "ringb" in res.output and "ringc" in res.output


def test_unknown_server_provider_degrades_when_none_listed(monkeypatch):
    # Older server / offline: no providers listed -> do not block on --provider.
    monkeypatch.setattr(oauth_native, "fetch_providers", lambda server, **kw: [])
    monkeypatch.setattr(oauth_native, "can_open_browser", lambda: True)

    calls = {}

    def _fake_loopback(server, **kw):
        calls["provider"] = kw.get("provider")
        raise oauth_native.LoginError("stop here (validation passed)")

    monkeypatch.setattr(oauth_native, "loopback_login", _fake_loopback)

    res = CliRunner().invoke(
        cli, ["login", "--server", "https://old.example", "--provider", "whatever"]
    )
    # Validation was skipped (empty list) and the flow proceeded to loopback_login,
    # carrying the provider through unchanged.
    assert calls.get("provider") == "whatever"
    assert "stop here" in res.output
