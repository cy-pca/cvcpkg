# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The client credential store (``cvcpkg login`` sessions)."""

from __future__ import annotations

import datetime
import os

import pytest

from cvcpkg import credentials
from cvcpkg.credentials import Credential


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "credentials.yaml"
    monkeypatch.setenv("CVCPKG_CREDENTIALS_FILE", str(path))
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    return path


def _iso(delta_seconds: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delta_seconds)
    ).isoformat()


def _cred(**kw) -> Credential:
    base = dict(
        access="cvcses_abc",
        refresh="cvcref_xyz",
        expires_at=_iso(3600),
        refresh_expires_at=_iso(86400),
        principal="joe",
        role="publisher",
        server_url="https://cvcpkg.example",
    )
    base.update(kw)
    return Credential(**base)


def test_save_get_roundtrip(store):
    credentials.save("cvcpkg.example", _cred())
    got = credentials.get("cvcpkg.example")
    assert got is not None
    assert got.access == "cvcses_abc" and got.principal == "joe"
    # Host lookup is case-insensitive.
    assert credentials.get("CVCPKG.EXAMPLE") is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_file_is_0600(store):
    credentials.save("h", _cred())
    assert (store.stat().st_mode & 0o777) == 0o600


def test_remove(store):
    credentials.save("h", _cred())
    assert credentials.remove("h") is True
    assert credentials.get("h") is None
    assert credentials.remove("h") is False  # already gone


def test_token_for_returns_access_when_fresh(store):
    credentials.save("h", _cred(expires_at=_iso(3600)))
    assert credentials.token_for("h") == "cvcses_abc"


def test_token_for_refreshes_when_expired(store, monkeypatch):
    credentials.save("h", _cred(expires_at=_iso(-10)))  # already expired

    def fake_post(url, form, timeout=30.0):
        assert form["grant_type"] == "refresh_token"
        return {
            "access_token": "cvcses_new",
            "refresh_token": "cvcref_new",
            "expires_in": 3600,
            "refresh_expires_in": 86400,
            "principal": "joe",
            "role": "publisher",
        }

    monkeypatch.setattr(credentials, "_post_form", fake_post)
    assert credentials.token_for("h") == "cvcses_new"
    # The rotated refresh is persisted (dropping it would lock the user out).
    assert credentials.get("h").refresh == "cvcref_new"


def test_token_for_returns_stale_on_refresh_failure(store, monkeypatch):
    credentials.save("h", _cred(expires_at=_iso(-10)))
    monkeypatch.setattr(credentials, "_post_form", lambda *a, **k: None)
    # Best-effort: hand back the stale token so the server's 401 surfaces.
    assert credentials.token_for("h") == "cvcses_abc"


def test_refresh_refused_when_refresh_expired(store):
    credentials.save("h", _cred(refresh_expires_at=_iso(-10)))
    assert credentials.refresh("h", credentials.get("h")) is None


def test_resolve_token_precedence(store, monkeypatch):
    from cvcpkg.cli._helpers import resolve_token

    credentials.save("cvcpkg.example", _cred(access="cvcses_stored"))
    server = "https://cvcpkg.example"
    # explicit flag wins over everything
    monkeypatch.setenv("CVCPKG_TOKEN", "cvctok_env")
    assert resolve_token("cvctok_flag", server) == "cvctok_flag"
    # env wins over the stored session
    assert resolve_token("", server) == "cvctok_env"
    # stored session is the fallback
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    assert resolve_token("", server) == "cvcses_stored"
    # a different host has no stored credential
    assert resolve_token("", "https://other.example") == ""
