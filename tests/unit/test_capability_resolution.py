"""Tests for capability-ranked virtual-package selection.

Covers the host capability probe (``cvcpkg.platform.host_capabilities``), the
resolver's provider map + capability filter/ranking, and round-tripping of the
``provides`` / ``requires_capabilities`` metadata through the manifest and
release-index parsers.
"""

from __future__ import annotations

import pytest

from cvcpkg import platform as platform_mod
from cvcpkg.errors import ResolveError
from cvcpkg.manifest import BundleManifest, CatalogEntry, ComponentReq, Dependency, ReleaseIndex
from cvcpkg.resolver import resolve


def _entry(
    name: str,
    version: str = "1.0.0+cvc.1",
    *,
    provides: list[str] | None = None,
    requires_capabilities: list[str] | None = None,
    deps: list[Dependency] | None = None,
) -> CatalogEntry:
    """Build a minimal CatalogEntry for resolver tests."""
    return CatalogEntry(
        name=name,
        version=version,
        upstream_version=version.split("+")[0],
        cvc_revision=1,
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256="0" * 64,
        size_bytes=100,
        archive_url=f"https://example.com/{name}-{version}.tar.gz",
        source_release="1.0.0",
        required_deps=deps or [],
        provides=provides or [],
        requires_capabilities=requires_capabilities or [],
    )


# ── Host capability probe ───────────────────────────────────────


class TestHostCapabilities:
    def test_env_override_parsed(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "cuda, foo")
        assert platform_mod.host_capabilities() == {"cuda", "foo"}

    def test_env_empty_means_no_capabilities(self, monkeypatch):
        # Authoritative: an empty string is "no capabilities", not "probe".
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        assert platform_mod.host_capabilities() == set()

    def test_probe_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(platform_mod, "_probed_capabilities", None)
        monkeypatch.setattr(platform_mod, "_CAPABILITY_PROBES", {"cuda": lambda: True})
        assert platform_mod.host_capabilities() == {"cuda"}

    def test_probe_absent(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(platform_mod, "_probed_capabilities", None)
        monkeypatch.setattr(platform_mod, "_CAPABILITY_PROBES", {"cuda": lambda: False})
        assert platform_mod.host_capabilities() == set()

    def test_probe_never_raises(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(platform_mod, "_probed_capabilities", None)

        def _boom() -> bool:
            raise RuntimeError("nvidia-smi exploded")

        monkeypatch.setattr(platform_mod, "_CAPABILITY_PROBES", {"cuda": _boom})
        assert platform_mod.host_capabilities() == set()

    def test_returned_set_is_a_copy(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.setattr(platform_mod, "_probed_capabilities", None)
        monkeypatch.setattr(platform_mod, "_CAPABILITY_PROBES", {"cuda": lambda: True})
        caps = platform_mod.host_capabilities()
        caps.add("mutated")
        # Mutating the returned set must not poison the cache.
        assert platform_mod.host_capabilities() == {"cuda"}


# ── Virtual-package resolution ──────────────────────────────────


def _virtual_candidates() -> dict[str, list[CatalogEntry]]:
    """app depends on the virtual ``libcvc``; two concrete providers exist."""
    return {
        "app": [_entry("app", deps=[Dependency(name="libcvc")])],
        "libcvc-cuda": [_entry("libcvc-cuda", provides=["libcvc"], requires_capabilities=["cuda"])],
        "libcvc-cpu": [_entry("libcvc-cpu", provides=["libcvc"])],
    }


class TestVirtualResolution:
    def test_cuda_host_prefers_cuda_provider(self):
        reqs = [ComponentReq(name="app")]
        result = resolve(reqs, _virtual_candidates(), capabilities={"cuda"})
        assert "app" in result.picked
        assert result.picked["libcvc"].name == "libcvc-cuda"

    def test_non_cuda_host_falls_back_to_cpu(self):
        reqs = [ComponentReq(name="app")]
        result = resolve(reqs, _virtual_candidates(), capabilities=set())
        assert result.picked["libcvc"].name == "libcvc-cpu"

    def test_top_level_virtual_request(self):
        reqs = [ComponentReq(name="libcvc")]
        result = resolve(reqs, _virtual_candidates(), capabilities={"cuda"})
        assert result.picked["libcvc"].name == "libcvc-cuda"

    def test_no_fallback_raises_capability_error(self):
        candidates = {
            "app": [_entry("app", deps=[Dependency(name="libcvc")])],
            "libcvc-cuda": [
                _entry("libcvc-cuda", provides=["libcvc"], requires_capabilities=["cuda"])
            ],
        }
        with pytest.raises(ResolveError, match="cuda"):
            resolve([ComponentReq(name="app")], candidates, capabilities=set())

    def test_capability_beats_higher_version(self):
        # The cpu provider is a *higher* version, yet the cuda provider must win
        # on a cuda host: capability rank dominates version ordering.
        candidates = {
            "libcvc-cuda": [
                _entry(
                    "libcvc-cuda",
                    "1.0.0+cvc.1",
                    provides=["libcvc"],
                    requires_capabilities=["cuda"],
                )
            ],
            "libcvc-cpu": [_entry("libcvc-cpu", "9.9.9+cvc.1", provides=["libcvc"])],
        }
        result = resolve([ComponentReq(name="libcvc")], candidates, capabilities={"cuda"})
        assert result.picked["libcvc"].name == "libcvc-cuda"

    def test_version_ordering_preserved_among_equal_capabilities(self):
        # Two cpu providers of the same virtual name: highest version wins.
        candidates = {
            "libcvc-cpu": [
                _entry("libcvc-cpu", "1.0.0+cvc.1", provides=["libcvc"]),
                _entry("libcvc-cpu", "2.0.0+cvc.1", provides=["libcvc"]),
            ],
        }
        result = resolve([ComponentReq(name="libcvc")], candidates, capabilities=set())
        assert result.picked["libcvc"].version == "2.0.0+cvc.1"


class TestExplicitConcreteRequest:
    def test_concrete_name_respects_capability_filter(self):
        candidates = _virtual_candidates()
        # cuda present → the concrete cuda bundle installs directly.
        ok = resolve([ComponentReq(name="libcvc-cuda")], candidates, capabilities={"cuda"})
        assert ok.picked["libcvc-cuda"].name == "libcvc-cuda"
        # cuda absent → the concrete cuda bundle is filtered out.
        with pytest.raises(ResolveError, match="cuda"):
            resolve([ComponentReq(name="libcvc-cuda")], candidates, capabilities=set())


class TestPlainPackagesUnaffected:
    def test_plain_package_still_resolves(self):
        candidates = {"zlib": [_entry("zlib", "1.3.1+cvc.1")]}
        result = resolve([ComponentReq(name="zlib")], candidates, capabilities=set())
        assert result.picked["zlib"].version == "1.3.1+cvc.1"

    def test_default_capabilities_probe_used(self, monkeypatch):
        # When capabilities is None the resolver falls back to the host probe.
        monkeypatch.setattr(platform_mod, "_probed_capabilities", None)
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "cuda")
        result = resolve([ComponentReq(name="libcvc")], _virtual_candidates())
        assert result.picked["libcvc"].name == "libcvc-cuda"


# ── Metadata round-trips ────────────────────────────────────────


class TestProvidesRoundTrip:
    def test_release_index_from_dict(self):
        d = {
            "bundles": [
                {
                    "name": "libcvc-cuda",
                    "version": "1.0.0+cvc.1",
                    "provides": ["libcvc"],
                    "requires_capabilities": ["cuda"],
                },
            ],
        }
        idx = ReleaseIndex.from_dict(d)
        assert idx.bundles[0].provides == ["libcvc"]
        assert idx.bundles[0].requires_capabilities == ["cuda"]

    def test_release_index_defaults_empty(self):
        idx = ReleaseIndex.from_dict({"bundles": [{"name": "zlib", "version": "1.3.1+cvc.1"}]})
        assert idx.bundles[0].provides == []
        assert idx.bundles[0].requires_capabilities == []

    def test_bundle_manifest_top_level(self):
        d = {
            "schema_version": 3,
            "bundle": {
                "name": "libcvc-cuda",
                "version": "1.0.0+cvc.1",
                "upstream_version": "1.0.0",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
            },
            "provides": ["libcvc"],
            "requires_capabilities": ["cuda"],
        }
        m = BundleManifest.from_dict(d)
        assert m.provides == ["libcvc"]
        assert m.requires_capabilities == ["cuda"]

    def test_bundle_manifest_requires_capabilities_nested_fallback(self):
        d = {
            "schema_version": 3,
            "bundle": {
                "name": "libcvc-cuda",
                "version": "1.0.0+cvc.1",
                "upstream_version": "1.0.0",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "requires_capabilities": ["cuda"],
            },
        }
        m = BundleManifest.from_dict(d)
        assert m.requires_capabilities == ["cuda"]

    def test_generate_manifest_emits_capabilities(self, tmp_path):
        import yaml

        from cvcpkg.builder import Recipe, generate_manifest

        recipe_dir = tmp_path / "recipes" / "libcvc-cuda"
        recipe_dir.mkdir(parents=True)
        recipe_raw = {
            "schema_version": 1,
            "recipe": {"name": "libcvc-cuda", "upstream_version": "1.0", "cvc_revision": 1},
            "source": {"type": "vendored", "path": "."},
            "provides": ["libcvc"],
            "requires_capabilities": ["cuda"],
            "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            "package": {"files": ["lib/*"]},
        }
        (recipe_dir / "recipe.yaml").write_text(yaml.dump(recipe_raw))
        (recipe_dir / "build.sh").write_text("#!/bin/sh\ntrue\n")

        r = Recipe.load(recipe_dir)
        assert r.provides == ["libcvc"]
        assert r.requires_capabilities == ["cuda"]

        install_dir = tmp_path / "install"
        (install_dir / "lib").mkdir(parents=True)
        (install_dir / "lib" / "libcvc.so").write_text("lib")

        manifest_dict = generate_manifest(r, install_dir, "linux", "x86_64", "release", "shared")
        # Round-trips through BundleManifest (top-level keys).
        m = BundleManifest.from_dict(manifest_dict)
        assert m.provides == ["libcvc"]
        assert m.requires_capabilities == ["cuda"]
