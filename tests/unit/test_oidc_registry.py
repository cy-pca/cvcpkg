# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Multi-issuer OIDC provider registry: parsing, back-compat, and guards.

The registry lets one cvcpkg instance be an OIDC client of several issuers at
once (tx.wtf sites / federation rings).  The bare ``CVCPKG_OIDC_*`` vars stay
provider ``default`` (zero-migration); extras arrive as a single JSON env var so
docker-compose ``${VAR:-}`` passthrough stays one line.
"""

from __future__ import annotations

import json

import pytest

from cvcpkg.server.oidc import (
    OidcConfig,
    load_providers,
    validate_registry,
)

_BARE = (
    "CVCPKG_OIDC_ISSUER",
    "CVCPKG_OIDC_CLIENT_ID",
    "CVCPKG_OIDC_CLIENT_SECRET",
    "CVCPKG_OIDC_REDIRECT_URL",
    "CVCPKG_OIDC_SCOPES",
    "CVCPKG_OIDC_GROUPS_CLAIM",
    "CVCPKG_OIDC_ADMIN_GROUPS",
    "CVCPKG_OIDC_PUBLISHER_GROUPS",
    "CVCPKG_OIDC_READER_GROUPS",
    "CVCPKG_OIDC_ADMIN_EMAILS",
    "CVCPKG_OIDC_DEFAULT_ROLE",
    "CVCPKG_OIDC_DISPLAY_NAME",
    "CVCPKG_OIDC_EXTRA_PROVIDERS",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for v in _BARE:
        monkeypatch.delenv(v, raising=False)


def _set_default(monkeypatch, **over):
    monkeypatch.setenv("CVCPKG_OIDC_ISSUER", over.get("issuer", "https://tx.wtf"))
    monkeypatch.setenv("CVCPKG_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("CVCPKG_OIDC_CLIENT_SECRET", "sec")
    monkeypatch.setenv("CVCPKG_OIDC_REDIRECT_URL", "https://cvcpkg.org/auth/oidc/callback")


def _extra(**over):
    d = {
        "id": "ringb",
        "issuer": "https://ring-b.example",
        "client_id": "c2",
        "client_secret": "s2",
        "redirect_url": "https://cvcpkg.org/auth/oidc/callback",
    }
    d.update(over)
    return d


class TestRegistryParsing:
    def test_empty_when_nothing_configured(self):
        assert load_providers() == []

    def test_bare_vars_become_default_provider(self, monkeypatch):
        _set_default(monkeypatch)
        providers = load_providers()
        assert [p.id for p in providers] == ["default"]
        assert providers[0].issuer == "https://tx.wtf"
        assert providers[0].display_name == "SSO"  # default label

    def test_display_name_env_used_for_default(self, monkeypatch):
        _set_default(monkeypatch)
        monkeypatch.setenv("CVCPKG_OIDC_DISPLAY_NAME", "tx.wtf")
        assert load_providers()[0].display_name == "tx.wtf"

    def test_extra_providers_added(self, monkeypatch):
        _set_default(monkeypatch)
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps([_extra()]))
        providers = load_providers()
        assert sorted(p.id for p in providers) == ["default", "ringb"]
        ringb = next(p for p in providers if p.id == "ringb")
        assert ringb.issuer == "https://ring-b.example"
        # display_name falls back to the id when not given
        assert ringb.display_name == "ringb"

    def test_extra_provider_without_default(self, monkeypatch):
        # An instance may serve ONLY extra providers (no bare vars set).
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps([_extra()]))
        assert [p.id for p in load_providers()] == ["ringb"]

    def test_extra_group_maps_as_list_or_csv(self, monkeypatch):
        monkeypatch.setenv(
            "CVCPKG_OIDC_EXTRA_PROVIDERS",
            json.dumps(
                [
                    _extra(admin_groups=["a", "b"], reader_groups="r1, r2 "),
                ]
            ),
        )
        p = load_providers()[0]
        assert p.admin_groups == frozenset({"a", "b"})
        assert p.reader_groups == frozenset({"r1", "r2"})

    def test_empty_extra_is_tolerated(self, monkeypatch):
        # docker-compose ${VAR:-} injects an unset var as "" — must not crash.
        _set_default(monkeypatch)
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", "")
        assert [p.id for p in load_providers()] == ["default"]


class TestRegistryLoudFailures:
    def test_malformed_json_raises(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", "{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_providers()

    def test_non_array_json_raises(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps({"id": "x"}))
        with pytest.raises(ValueError, match="must be a JSON array"):
            load_providers()

    def test_entry_missing_id_raises(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps([_extra(id="")]))
        with pytest.raises(ValueError, match="non-empty 'id'"):
            load_providers()

    def test_entry_reusing_default_id_raises(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps([_extra(id="default")]))
        with pytest.raises(ValueError, match="may not use id 'default'"):
            load_providers()

    def test_half_configured_extra_raises(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps([_extra(client_secret="")]))
        with pytest.raises(ValueError, match="missing required field.*client_secret"):
            load_providers()


class TestRegistryValidation:
    def test_clean_registry_has_no_problems(self, monkeypatch):
        _set_default(monkeypatch)
        monkeypatch.setenv("CVCPKG_OIDC_EXTRA_PROVIDERS", json.dumps([_extra()]))
        assert validate_registry(load_providers()) == []

    def test_duplicate_issuer_refused(self):
        a = OidcConfig(
            issuer="https://same",
            client_id="c",
            client_secret="s",
            redirect_url="r",
            id="a",
        )
        b = OidcConfig(
            issuer="https://same",
            client_id="c",
            client_secret="s",
            redirect_url="r",
            id="b",
        )
        problems = validate_registry([a, b])
        assert any("share issuer" in p for p in problems)

    def test_universal_group_guard_runs_per_provider(self):
        # A provider that maps admin onto a group everybody holds is refused — the
        # guard runs for each provider, not just default.
        bad = OidcConfig(
            issuer="https://ring",
            client_id="c",
            client_secret="s",
            redirect_url="r",
            id="ringb",
            groups_claim="groups",
            admin_groups=frozenset({"user"}),
        )
        problems = validate_registry([bad])
        assert any("every account" in p for p in problems)
