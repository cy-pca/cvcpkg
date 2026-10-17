"""Tests for root-authoritative top-down resolution (roadmap Phase 12, inc. 3)."""

from __future__ import annotations

import pytest

from cvcpkg import catalog as catalog_mod
from cvcpkg.root_resolution import merge_root_authoritative


def _cat(*bundles, **extra):
    return {"schema_version": 1, "bundles": list(bundles), **extra}


def _pub(name, version="1.0", **kw):
    return {"name": name, "version": version, "org": "", **kw}


def _org(name, org, version="1.0", **kw):
    return {"name": name, "version": version, "org": org, **kw}


class TestMerge:
    def test_public_from_root_org_from_local(self):
        root = _cat(_pub("zlib", "1.3"), _pub("boost", "1.90"))
        local = _cat(
            _pub("zlib", "1.2"),  # stale local public copy — must be overridden
            _org("iqi", "shell"),  # org package — kept
        )
        merged = merge_root_authoritative(root, local)
        names = {(b["name"], b["version"], b["org"]) for b in merged["bundles"]}
        # public zlib comes from root (1.3), not the stale local 1.2
        assert ("zlib", "1.3", "") in names
        assert ("zlib", "1.2", "") not in names
        assert ("boost", "1.90", "") in names  # root-only public present
        assert ("iqi", "1.0", "shell") in names  # local org present

    def test_root_public_only_no_local_public_leaks(self):
        # A public package that exists only locally is NOT authoritative.
        root = _cat(_pub("zlib"))
        local = _cat(_pub("zlib"), _pub("sneaky-local-public"))
        merged = merge_root_authoritative(root, local)
        names = {b["name"] for b in merged["bundles"]}
        assert "sneaky-local-public" not in names
        assert "zlib" in names

    def test_root_unreachable_falls_back_to_local(self):
        local = _cat(_pub("zlib"), _org("iqi", "shell"))
        merged = merge_root_authoritative(None, local)
        assert merged is local  # offline: local mirror used verbatim

    def test_local_none_returns_root(self):
        root = _cat(_pub("zlib"))
        assert merge_root_authoritative(root, None) is root

    def test_both_none_is_empty(self):
        assert merge_root_authoritative(None, None) == {"bundles": []}

    def test_preserves_local_toplevel_metadata(self):
        root = _cat(_pub("zlib"))
        local = _cat(_org("iqi", "shell"), generated_at="2026-07-16")
        merged = merge_root_authoritative(root, local)
        assert merged["generated_at"] == "2026-07-16"


class TestFetchAuthoritative:
    def test_noop_when_root_equals_server(self, monkeypatch):
        calls = []

        def fake_fetch(url, **kw):
            calls.append(url)
            return _cat(_pub("zlib"))

        monkeypatch.setattr(catalog_mod, "fetch_catalog", fake_fetch)
        catalog_mod.fetch_authoritative_catalog(
            server_catalog_url="https://x/v1/catalog",
            root_catalog_url="https://x/v1/catalog",
        )
        assert calls == ["https://x/v1/catalog"]  # single fetch, no merge

    def test_merges_distinct_root_and_server(self, monkeypatch):
        def fake_fetch(url, **kw):
            if "root" in url:
                return _cat(_pub("zlib", "1.3"))
            return _cat(_pub("zlib", "1.2"), _org("iqi", "shell"))

        monkeypatch.setattr(catalog_mod, "fetch_catalog", fake_fetch)
        cat = catalog_mod.fetch_authoritative_catalog(
            server_catalog_url="https://edge/v1/catalog",
            root_catalog_url="https://root/v1/catalog",
        )
        names = {(b["name"], b["version"], b["org"]) for b in cat["bundles"]}
        assert ("zlib", "1.3", "") in names  # public authoritative from root
        assert ("iqi", "1.0", "shell") in names  # org from local

    def test_offline_root_falls_back_to_local(self, monkeypatch):
        def fake_fetch(url, **kw):
            if "root" in url:
                raise catalog_mod.CatalogError("root down")
            return _cat(_pub("zlib", "1.2"), _org("iqi", "shell"))

        monkeypatch.setattr(catalog_mod, "fetch_catalog", fake_fetch)
        cat = catalog_mod.fetch_authoritative_catalog(
            server_catalog_url="https://edge/v1/catalog",
            root_catalog_url="https://root/v1/catalog",
        )
        names = {b["name"] for b in cat["bundles"]}
        assert names == {"zlib", "iqi"}  # local mirror used offline

    def test_both_down_raises(self, monkeypatch):
        def fake_fetch(url, **kw):
            raise catalog_mod.CatalogError("down")

        monkeypatch.setattr(catalog_mod, "fetch_catalog", fake_fetch)
        with pytest.raises(catalog_mod.CatalogError):
            catalog_mod.fetch_authoritative_catalog(
                server_catalog_url="https://edge/v1/catalog",
                root_catalog_url="https://root/v1/catalog",
            )


def _plat(name, platform, version="1.0.0+cvc.1", **kw):
    return {
        "name": name,
        "version": version,
        "org": "",
        "platform": platform,
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        **kw,
    }


class TestInstallNamesTheRoot:
    """A server's own public bundles are ignored while the root is another
    server (test_root_public_only_no_local_public_leaks), so pointing
    CVCPKG_SERVER_URL at a standalone registry fails on what only it has.  The
    error has to say where the public catalog came from, or it reads as "the
    server has no such bundle" (netbsd-build-2 against the dev registry)."""

    def _install(self, monkeypatch, tmp_path, root_bundles, server_bundles, *names, root=""):
        from click.testing import CliRunner

        from cvcpkg.cli import cli

        def fake_fetch(url, **kw):
            return _cat(*(server_bundles if "edge.invalid" in url else root_bundles))

        monkeypatch.setattr(catalog_mod, "fetch_catalog", fake_fetch)
        for var in ("CVCPKG_CATALOG_URL", "CVCPKG_ROOT_CATALOG_URL"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CVCPKG_SERVER_URL", "http://edge.invalid:8420")
        if root:
            monkeypatch.setenv("CVCPKG_ROOT_URL", root)
        else:
            monkeypatch.delenv("CVCPKG_ROOT_URL", raising=False)
        argv = [
            "install",
            *names,
            "--prefix",
            str(tmp_path / "px"),
            "--platform",
            "netbsd",
            "--arch",
            "x86_64",
        ]
        return CliRunner().invoke(cli, argv)

    def test_nothing_for_the_tuple_names_the_root(self, monkeypatch, tmp_path):
        result = self._install(
            monkeypatch, tmp_path, [_plat("zlib", "linux")], [_plat("zlib", "netbsd")], "zlib"
        )
        assert result.exit_code != 0
        assert "no bundles found in catalog for this platform tuple." in result.output
        assert "Public packages resolve from the root registry https://cvcpkg.org" in result.output
        assert "CVCPKG_ROOT_URL=http://edge.invalid:8420" in result.output

    def test_unsatisfiable_dependency_names_the_root(self, monkeypatch, tmp_path):
        from cvcpkg.errors import ResolveError

        root = [
            _plat("attrs-cp312", "any", arch="noarch", required_deps=[{"name": "python312"}]),
            _plat("python312", "linux"),
        ]
        result = self._install(
            monkeypatch, tmp_path, root, [_plat("python312", "netbsd")], "attrs-cp312"
        )
        assert isinstance(result.exception, ResolveError), result.output
        assert "python312" in str(result.exception)
        assert "CVCPKG_ROOT_URL=http://edge.invalid:8420" in str(result.exception)

    def test_no_hint_when_the_server_is_the_root(self, monkeypatch, tmp_path):
        result = self._install(
            monkeypatch,
            tmp_path,
            [],
            [_plat("zlib", "linux")],
            "zlib",
            root="http://edge.invalid:8420",
        )
        assert "no bundles found in catalog for this platform tuple." in result.output
        assert "CVCPKG_ROOT_URL" not in result.output
