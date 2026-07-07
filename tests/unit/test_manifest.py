"""Tests for cvcpkg.manifest — data models for manifests, indexes, requirements."""

from __future__ import annotations

import pytest

from cvcpkg.errors import SchemaError
from cvcpkg.manifest import (
    BundleManifest,
    CatalogEntry,
    ReleaseIndex,
    Requirements,
)

# ── BundleManifest ──────────────────────────────────────────────


class TestBundleManifestFromDict:
    """BundleManifest.from_dict(d) parsing."""

    MINIMAL = {
        "schema_version": 3,
        "bundle": {
            "name": "zlib",
            "version": "1.3.1+cvc.1",
            "upstream_version": "1.3.1",
            "cvc_revision": 1,
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
        },
    }

    def test_minimal(self):
        m = BundleManifest.from_dict(self.MINIMAL)
        assert m.name == "zlib"
        assert m.version == "1.3.1+cvc.1"
        assert m.platform == "linux"
        assert m.arch == "x86_64"
        assert m.build_type == "release"
        assert m.link == "shared"
        assert m.cvc_revision == 1

    def test_schema_version_1(self):
        d = {**self.MINIMAL, "schema_version": 1}
        m = BundleManifest.from_dict(d)
        assert m.schema_version == 1

    def test_schema_version_2(self):
        d = {**self.MINIMAL, "schema_version": 2}
        m = BundleManifest.from_dict(d)
        assert m.schema_version == 2

    def test_unsupported_schema_version(self):
        d = {**self.MINIMAL, "schema_version": 99}
        with pytest.raises(SchemaError, match="unsupported manifest schema_version"):
            BundleManifest.from_dict(d)

    def test_missing_schema_version(self):
        d = {**self.MINIMAL}
        del d["schema_version"]
        with pytest.raises(SchemaError):
            BundleManifest.from_dict(d)

    def test_abi_parsed(self):
        d = {**self.MINIMAL}
        d["bundle"] = {
            **d["bundle"],
            "abi": {
                "cxx_std": 20,
                "cxx_runtime": "libstdc++",
                "libc": "glibc-2.31",
                "crt_link": "dynamic",
            },
        }
        m = BundleManifest.from_dict(d)
        assert m.abi.cxx_std == 20
        assert m.abi.cxx_runtime == "libstdc++"
        assert m.abi.libc == "glibc-2.31"
        assert m.abi.crt_link == "dynamic"

    def test_abi_defaults(self):
        m = BundleManifest.from_dict(self.MINIMAL)
        assert m.abi.cxx_std == 17
        assert m.abi.cxx_runtime == ""
        assert m.abi.libc == ""

    def test_link_actual_defaults_to_link(self):
        m = BundleManifest.from_dict(self.MINIMAL)
        assert m.link_actual == "shared"

    def test_link_actual_explicit(self):
        d = {**self.MINIMAL}
        d["bundle"] = {**d["bundle"], "link_actual": "hybrid"}
        m = BundleManifest.from_dict(d)
        assert m.link_actual == "hybrid"

    def test_contents_parsed(self):
        d = {
            **self.MINIMAL,
            "contents": {
                "description": "zlib compression",
                "files": ["lib/libz.so", "include/zlib.h"],
                "cmake_packages": [{"name": "ZLIB", "targets": ["ZLIB::ZLIB"]}],
                "pkgconfig": ["zlib"],
                "tools": ["zlib-flate"],
            },
        }
        m = BundleManifest.from_dict(d)
        assert m.description == "zlib compression"
        assert len(m.files) == 2
        assert m.cmake_packages[0].name == "ZLIB"
        assert m.cmake_packages[0].targets == ["ZLIB::ZLIB"]
        assert m.pkgconfig == ["zlib"]
        assert m.tools == ["zlib-flate"]

    def test_dependencies_parsed(self):
        d = {
            **self.MINIMAL,
            "dependencies": {
                "required": [{"name": "gsl", "version": ">=2.7"}],
                "optional": [{"name": "hdf5", "reason": "file I/O"}],
            },
        }
        m = BundleManifest.from_dict(d)
        assert len(m.required_deps) == 1
        assert m.required_deps[0].name == "gsl"
        assert m.required_deps[0].version == ">=2.7"
        assert len(m.optional_deps) == 1
        assert m.optional_deps[0].reason == "file I/O"

    def test_integrity_parsed(self):
        d = {
            **self.MINIMAL,
            "integrity": {
                "sha256": "a" * 64,
                "size_bytes": 102400,
                "built_at": "2026-05-22T00:00:00Z",
            },
        }
        m = BundleManifest.from_dict(d)
        assert m.sha256 == "a" * 64
        assert m.size_bytes == 102400
        assert m.built_at == "2026-05-22T00:00:00Z"

    def test_empty_contents_ok(self):
        m = BundleManifest.from_dict(self.MINIMAL)
        assert m.files == []
        assert m.cmake_packages == []

    def test_from_yaml(self, tmp_path):
        import yaml

        p = tmp_path / "manifest.yaml"
        p.write_text(yaml.dump(self.MINIMAL))
        m = BundleManifest.from_yaml(str(p))
        assert m.name == "zlib"

    def test_legacy_depends_key_parsed(self):
        """Legacy flat 'depends' list is parsed into required_deps."""
        d = {
            **self.MINIMAL,
            "depends": [
                {"name": "gsl", "version": ">=2.7"},
                {"name": "fftw3"},
            ],
        }
        m = BundleManifest.from_dict(d)
        assert len(m.required_deps) == 2
        assert m.required_deps[0].name == "gsl"
        assert m.required_deps[0].version == ">=2.7"
        assert m.required_deps[1].name == "fftw3"

    def test_legacy_config_key_parsed(self):
        """Legacy 'config' field in bundle is parsed as build_type."""
        d = {**self.MINIMAL}
        b = dict(d["bundle"])
        del b["build_type"]
        b["config"] = "debug"
        d["bundle"] = b
        m = BundleManifest.from_dict(d)
        assert m.build_type == "debug"

    def test_generate_manifest_roundtrip(self, tmp_path):
        """Manifest produced by generate_manifest is parsed by BundleManifest."""
        from cvcpkg.builder import Recipe, generate_manifest

        recipe_dir = tmp_path / "recipes" / "testpkg"
        recipe_dir.mkdir(parents=True)
        import yaml

        recipe_raw = {
            "schema_version": 1,
            "recipe": {"name": "testpkg", "upstream_version": "2.0", "cvc_revision": 1},
            "source": {"type": "vendored", "path": "."},
            "depends": {
                "runtime": [
                    {"name": "zlib", "version": "^1.3"},
                    {"name": "boost"},
                ],
            },
            "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            "package": {"files": ["lib/*"]},
        }
        (recipe_dir / "recipe.yaml").write_text(yaml.dump(recipe_raw))
        (recipe_dir / "build.sh").write_text("#!/bin/sh\ntrue\n")

        r = Recipe.load(recipe_dir)
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / "lib").mkdir()
        (install_dir / "lib" / "libtest.so").write_text("lib")

        manifest_dict = generate_manifest(r, install_dir, "linux", "x86_64", "release", "shared")

        # Parse through BundleManifest
        m = BundleManifest.from_dict(manifest_dict)
        assert m.name == "testpkg"
        assert m.version == "2.0+cvc.1"
        assert m.platform == "linux"
        assert m.build_type == "release"
        assert len(m.required_deps) == 2
        assert m.required_deps[0].name == "zlib"
        assert m.required_deps[0].version == "^1.3"
        assert m.required_deps[1].name == "boost"
        assert m.built_at != ""
        assert m.description == ""
        assert "lib/libtest.so" in m.files


# ── CatalogEntry ────────────────────────────────────────────────


class TestCatalogEntry:
    def test_basic(self):
        e = CatalogEntry(
            name="boost",
            version="1.86.0+cvc.1",
            upstream_version="1.86.0",
            cvc_revision=1,
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            sha256="b" * 64,
            size_bytes=5000,
            archive_url="https://example.com/boost.tar.gz",
            source_release="1.2.0",
        )
        assert e.name == "boost"
        assert e.version == "1.86.0+cvc.1"


# ── ReleaseIndex ────────────────────────────────────────────────


class TestReleaseIndex:
    def test_from_dict(self):
        d = {
            "schema_version": 1,
            "release_version": "1.2.0",
            "recommended": {"zlib": "1.3.1+cvc.1"},
            "bundles": [
                {
                    "name": "zlib",
                    "version": "1.3.1+cvc.1",
                    "upstream_version": "1.3.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                    "sha256": "c" * 64,
                    "archive_url": "https://example.com/zlib.tar.gz",
                },
            ],
        }
        idx = ReleaseIndex.from_dict(d)
        assert idx.release_version == "1.2.0"
        assert idx.recommended == {"zlib": "1.3.1+cvc.1"}
        assert len(idx.bundles) == 1
        assert idx.bundles[0].name == "zlib"

    def test_from_dict_defaults(self):
        idx = ReleaseIndex.from_dict({})
        assert idx.schema_version == 1
        assert idx.release_version == ""
        assert idx.bundles == []

    def test_bundle_with_deps(self):
        d = {
            "bundles": [
                {
                    "name": "grpc",
                    "version": "1.76.0+cvc.1",
                    "required_deps": [{"name": "protobuf", "version": ">=28.0"}],
                },
            ],
        }
        idx = ReleaseIndex.from_dict(d)
        assert idx.bundles[0].required_deps[0].name == "protobuf"
        assert idx.bundles[0].required_deps[0].version == ">=28.0"


# ── Requirements ────────────────────────────────────────────────


class TestRequirements:
    def test_from_dict_defaults(self):
        r = Requirements.from_dict({})
        assert r.platform == "auto"
        assert r.arch == "auto"
        assert r.config == "release"
        assert r.link == "shared"
        assert r.components == []

    def test_string_components(self):
        r = Requirements.from_dict({"components": ["zlib", "boost"]})
        assert len(r.components) == 2
        assert r.components[0].name == "zlib"
        assert r.components[0].version == ""

    def test_dict_components(self):
        r = Requirements.from_dict(
            {
                "components": [
                    {"name": "zlib", "version": "==1.3.1"},
                    {"name": "vtk", "exclude": True},
                ]
            }
        )
        assert r.components[0].version == "==1.3.1"
        assert r.components[1].exclude is True

    def test_overrides(self):
        r = Requirements.from_dict({"overrides": [{"name": "boost", "version": "==1.85.0+cvc.2"}]})
        assert len(r.overrides) == 1
        assert r.overrides[0].name == "boost"

    def test_from_yaml(self, tmp_path):
        import yaml

        p = tmp_path / "reqs.yaml"
        p.write_text(
            yaml.dump(
                {
                    "platform": "linux",
                    "config": "debug",
                    "components": ["zlib", "boost"],
                }
            )
        )
        r = Requirements.from_yaml(str(p))
        assert r.platform == "linux"
        assert r.config == "debug"
        assert len(r.components) == 2

    def test_mixed_components(self):
        r = Requirements.from_dict(
            {
                "components": [
                    "zlib",
                    {"name": "boost", "version": "^1.80"},
                ]
            }
        )
        assert r.components[0].name == "zlib"
        assert r.components[0].version == ""
        assert r.components[1].name == "boost"
        assert r.components[1].version == "^1.80"


# ── Hardening: error paths ──────────────────────────────────────


class TestBundleManifestHardening:
    """Tests for hardened from_dict / from_yaml error handling."""

    def test_from_dict_missing_bundle_block(self):
        """from_dict raises SchemaError when 'bundle' has no required keys."""
        d = {"schema_version": 3}  # no bundle block at all
        with pytest.raises(SchemaError, match="missing required field"):
            BundleManifest.from_dict(d)

    def test_from_dict_missing_required_bundle_key(self):
        """from_dict raises SchemaError when a required bundle key is absent."""
        d = {
            "schema_version": 3,
            "bundle": {
                "name": "zlib",
                # missing version, upstream_version, etc.
            },
        }
        with pytest.raises(SchemaError, match="missing required field"):
            BundleManifest.from_dict(d)

    def test_from_yaml_nonexistent_file(self):
        """from_yaml raises SchemaError for a missing file."""
        with pytest.raises(SchemaError, match="not found"):
            BundleManifest.from_yaml("/tmp/no-such-manifest-xyz.yaml")

    def test_from_yaml_non_dict(self, tmp_path):
        """from_yaml raises SchemaError if the YAML is not a mapping."""
        p = tmp_path / "manifest.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(SchemaError, match="not a YAML mapping"):
            BundleManifest.from_yaml(str(p))

    def test_from_yaml_empty_file(self, tmp_path):
        """from_yaml raises SchemaError for an empty YAML file (None)."""
        p = tmp_path / "manifest.yaml"
        p.write_text("")
        with pytest.raises(SchemaError, match="not a YAML mapping"):
            BundleManifest.from_yaml(str(p))

    def test_from_yaml_scalar(self, tmp_path):
        """from_yaml raises SchemaError for a scalar YAML value."""
        p = tmp_path / "manifest.yaml"
        p.write_text("42\n")
        with pytest.raises(SchemaError, match="not a YAML mapping"):
            BundleManifest.from_yaml(str(p))
