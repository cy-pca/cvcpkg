"""Cross-server (federated) dependency resolution (cvcpkg.federation)."""

from __future__ import annotations

import pytest

from cvcpkg.federation import FederationError, resolve_federated


def _clean_env(monkeypatch):
    monkeypatch.delenv("CVCPKG_REGISTRIES", raising=False)
    monkeypatch.delenv("CVCPKG_REGISTRIES_FILE", raising=False)


def _registries(tmp_path):
    (tmp_path / "registries.yaml").write_text(
        "registries:\n"
        "  edge-b.lab:\n    url: http://edge-b:8420\n    token: tok-b\n"
        "  edge-c.lab:\n    url: http://edge-c:8420\n    token: tok-c\n"
    )


def _fake_get(catalog, recorder):
    def get(url, token, timeout=30.0):
        recorder.append((url, token))
        base, _, tail = url.partition("/v1/packages/")
        name = tail.split("?")[0]
        deps = catalog.get((base, name))
        if deps is None:
            raise AssertionError(f"unexpected fetch: {url}")
        return {"packages": [{"name": name, "org": "", "required_deps": deps}]}

    return get


def test_resolve_across_three_registries(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    _registries(tmp_path)
    catalog = {
        # app (local) -> edge-b private iqi-core -> edge-c public base
        ("http://local", "app"): [{"name": "iqi-core", "org": "shell", "server": "edge-b.lab"}],
        ("http://edge-b:8420", "iqi-core"): [
            {"name": "base", "org": "pub", "server": "edge-c.lab"}
        ],
        ("http://edge-c:8420", "base"): [],
    }
    used: list = []
    order = resolve_federated(
        "app",
        local_url="http://local",
        local_token="tok-a",
        config_dir=tmp_path,
        http_get=_fake_get(catalog, used),
    )

    # Post-order: dependencies before dependents.
    assert [n.ref.name for n in order] == ["base", "iqi-core", "app"]
    # Each node resolved on the right registry.
    assert [(n.ref.name, n.server, n.base_url) for n in order] == [
        ("base", "edge-c.lab", "http://edge-c:8420"),
        ("iqi-core", "edge-b.lab", "http://edge-b:8420"),
        ("app", "", "http://local"),
    ]
    # Each fetch used that registry's token (per-domain credentials).
    tok_by_base = {u.split("/v1")[0]: t for u, t in used}
    assert tok_by_base == {
        "http://local": "tok-a",
        "http://edge-b:8420": "tok-b",
        "http://edge-c:8420": "tok-c",
    }
    # org param is sent for org-scoped fetches.
    assert any("?org=shell" in u for u, _ in used)


def test_bare_deps_of_remote_stay_on_that_remote(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    _registries(tmp_path)
    catalog = {
        ("http://local", "app"): [{"name": "iqi-core", "org": "shell", "server": "edge-b.lab"}],
        # iqi-core's bare dep 'zlib' must resolve on edge-b, not locally.
        ("http://edge-b:8420", "iqi-core"): [{"name": "zlib"}],
        ("http://edge-b:8420", "zlib"): [],
    }
    used: list = []
    order = resolve_federated(
        "app",
        local_url="http://local",
        config_dir=tmp_path,
        http_get=_fake_get(catalog, used),
    )
    zlib = next(n for n in order if n.ref.name == "zlib")
    assert zlib.server == "edge-b.lab" and zlib.base_url == "http://edge-b:8420"


def test_unallowlisted_host_is_refused(tmp_path, monkeypatch):
    _clean_env(monkeypatch)  # no registries.yaml -> empty allowlist
    catalog = {
        ("http://local", "app"): [{"name": "x", "server": "evil.example"}],
    }
    with pytest.raises(FederationError, match="allowlist"):
        resolve_federated(
            "app",
            local_url="http://local",
            config_dir=tmp_path,
            http_get=_fake_get(catalog, []),
        )


def test_diamond_dep_resolved_once(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    _registries(tmp_path)
    catalog = {
        ("http://local", "app"): [
            {"name": "a", "server": "edge-b.lab"},
            {"name": "b", "server": "edge-b.lab"},
        ],
        ("http://edge-b:8420", "a"): [{"name": "common"}],
        ("http://edge-b:8420", "b"): [{"name": "common"}],
        ("http://edge-b:8420", "common"): [],
    }
    used: list = []
    order = resolve_federated(
        "app",
        local_url="http://local",
        config_dir=tmp_path,
        http_get=_fake_get(catalog, used),
    )
    assert [n.ref.name for n in order].count("common") == 1  # visited once
    # 'common' fetched exactly once.
    assert sum(1 for u, _ in used if u.endswith("/v1/packages/common")) == 1


def test_repr_does_not_leak_token(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    _registries(tmp_path)
    catalog = {("http://local", "app"): []}
    order = resolve_federated(
        "app", local_url="http://local", local_token="s3cret", config_dir=tmp_path,
        http_get=_fake_get(catalog, []),
    )
    assert "s3cret" not in repr(order[0])
