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
from cvcpkg.cli import _auth, cli


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


# ── provider menu: default matches the server's default, selection == display ──
#
# Observed against cvcpkg.org: /v1/auth/providers is id-sorted (cvcpkg.com before
# tx.wtf) and the prompt defaulted to [1] = cvcpkg.com, while the real default is
# tx.wtf (id "default").  A user who accepted the default was sent to cvcpkg.com,
# where their tx.wtf identity has no account, so the login never completed.


def test_default_index_prefers_the_marker():
    # Even when the default is not listed first, the marker wins.
    choices = [
        {"id": "cvcpkg-com", "display_name": "cvcpkg.com", "default": False},
        {"id": "default", "display_name": "tx.wtf", "default": True},
    ]
    assert _auth._default_provider_index(choices) == 2


def test_default_index_falls_back_to_the_default_slug_without_a_marker():
    # Older server: lists providers but sends no marker; the conventional
    # "default" slug (the bare CVCPKG_OIDC_* provider) still resolves.
    assert _auth._default_provider_index([{"id": "cvcpkg-com"}, {"id": "default"}]) == 2


def test_default_index_falls_back_to_first_when_nothing_marks_a_default():
    assert _auth._default_provider_index([{"id": "ringa"}, {"id": "ringb"}]) == 1


def test_prompt_default_is_the_marked_provider_not_the_first(monkeypatch):
    # Regression: id-sorted order (cvcpkg.com first) but tx.wtf is the default.
    # Pressing Enter must pick tx.wtf, and the [default] marker shows on its row.
    choices = [
        {"id": "cvcpkg-com", "display_name": "cvcpkg.com", "default": False},
        {"id": "default", "display_name": "tx.wtf", "default": True},
    ]
    seen = {}
    lines: list[str] = []

    def fake_prompt(text, **kw):
        seen["default"] = kw.get("default")
        return kw.get("default")  # user presses Enter -> take the default

    monkeypatch.setattr(_auth.click, "prompt", fake_prompt)
    monkeypatch.setattr(_auth.click, "echo", lambda *a, **k: lines.append(a[0] if a else ""))

    assert _auth._prompt_provider(choices) == "default"
    assert seen["default"] == 2
    assert any("tx.wtf" in ln and "[default]" in ln for ln in lines)


def test_prompt_selection_maps_to_displayed_order(monkeypatch):
    # The numeric choice indexes the list as displayed; it is never reordered.
    choices = [
        {"id": "default", "display_name": "tx.wtf", "default": True},
        {"id": "cvcpkg-com", "display_name": "cvcpkg.com", "default": False},
    ]
    monkeypatch.setattr(_auth.click, "echo", lambda *a, **k: None)
    monkeypatch.setattr(_auth.click, "prompt", lambda text, **kw: 2)
    assert _auth._prompt_provider(choices) == "cvcpkg-com"
    monkeypatch.setattr(_auth.click, "prompt", lambda text, **kw: 1)
    assert _auth._prompt_provider(choices) == "default"
