"""Tests for cvcpkg.catalog — fetch, cache, filter, and generate catalog entries."""

from __future__ import annotations

import hashlib

import yaml

from cvcpkg.catalog import catalog_entries, generate_catalog, load_catalog_from_file
from cvcpkg.manifest import CatalogEntry

# ── load_catalog_from_file ──────────────────────────────────────


class TestLoadCatalogFromFile:
    def test_load(self, tmp_path):
        p = tmp_path / "catalog.yaml"
        data = {
            "schema_version": 1,
            "bundles": [
                {
                    "name": "zlib",
                    "version": "1.3.1+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                    "sha256": "a" * 64,
                    "archive_url": "https://example.com/zlib.tar.gz",
                },
                {
                    "name": "boost",
                    "version": "1.86.0+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                    "sha256": "b" * 64,
                    "archive_url": "https://example.com/boost.tar.gz",
                },
            ],
        }
        p.write_text(yaml.dump(data))
        cat = load_catalog_from_file(p)
        assert cat["schema_version"] == 1
        assert len(cat["bundles"]) == 2


# ── catalog_entries ─────────────────────────────────────────────


class TestCatalogEntries:
    CATALOG = {
        "bundles": [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "a" * 64,
                "archive_url": "https://example.com/zlib-linux.tar.gz",
            },
            {
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "platform": "windows",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "b" * 64,
                "archive_url": "https://example.com/zlib-win.zip",
            },
            {
                "name": "boost",
                "version": "1.86.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "c" * 64,
                "archive_url": "https://example.com/boost.tar.gz",
            },
            {
                "name": "boost",
                "version": "1.86.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "debug",
                "link": "shared",
                "sha256": "d" * 64,
                "archive_url": "https://example.com/boost-dbg.tar.gz",
            },
        ],
    }

    def test_unfiltered(self):
        entries = catalog_entries(self.CATALOG)
        assert len(entries) == 4
        assert all(isinstance(e, CatalogEntry) for e in entries)

    def test_filter_platform(self):
        entries = catalog_entries(self.CATALOG, platform="linux")
        assert len(entries) == 3
        assert all(e.platform == "linux" for e in entries)

    def test_filter_platform_windows(self):
        entries = catalog_entries(self.CATALOG, platform="windows")
        assert len(entries) == 1
        assert entries[0].name == "zlib"

    def test_filter_build_type(self):
        entries = catalog_entries(self.CATALOG, build_type="debug")
        assert len(entries) == 1
        assert entries[0].name == "boost"

    def test_filter_combined(self):
        entries = catalog_entries(
            self.CATALOG, platform="linux", build_type="release", link="shared"
        )
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert names == {"zlib", "boost"}

    def test_filter_no_match(self):
        entries = catalog_entries(self.CATALOG, platform="macos")
        assert entries == []

    def test_entry_deps(self):
        catalog = {
            "bundles": [
                {
                    "name": "grpc",
                    "version": "1.76.0+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                    "sha256": "e" * 64,
                    "archive_url": "https://example.com/grpc.tar.gz",
                    "required_deps": [
                        {"name": "protobuf", "version": ">=28.0"},
                        {"name": "zlib"},
                    ],
                },
            ],
        }
        entries = catalog_entries(catalog)
        assert len(entries[0].required_deps) == 2
        assert entries[0].required_deps[0].name == "protobuf"
        assert entries[0].required_deps[0].version == ">=28.0"
        assert entries[0].required_deps[1].name == "zlib"

    def test_empty_catalog(self):
        assert catalog_entries({}) == []
        assert catalog_entries({"bundles": []}) == []

    # A noarch bundle (platform=any/arch=noarch) is valid on every host, so a
    # concrete-host query must surface it — otherwise it publishes but never
    # resolves/installs.
    NOARCH_CATALOG = {
        "bundles": [
            {
                "name": "idna",
                "version": "3.16+cvc.1",
                "platform": "any",
                "arch": "noarch",
                "build_type": "release",
                "link": "shared",
                "sha256": "f" * 64,
                "archive_url": "https://example.com/idna-noarch.tar.gz",
            },
            {
                "name": "numpy-cp311",
                "version": "2.2.0+cvc.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "e" * 64,
                "archive_url": "https://example.com/numpy-linux.tar.gz",
            },
        ],
    }

    def test_noarch_matches_concrete_linux_host(self):
        entries = catalog_entries(self.NOARCH_CATALOG, platform="linux", arch="x86_64")
        names = {e.name for e in entries}
        assert names == {"idna", "numpy-cp311"}  # noarch idna surfaces alongside the concrete one

    def test_noarch_matches_a_different_host(self):
        # Same noarch bundle is valid on windows/arm64 too; the concrete
        # linux/x86_64 numpy is not.
        entries = catalog_entries(self.NOARCH_CATALOG, platform="windows", arch="arm64")
        assert {e.name for e in entries} == {"idna"}

    def test_noarch_query_still_finds_noarch(self):
        entries = catalog_entries(self.NOARCH_CATALOG, platform="any", arch="noarch")
        assert {e.name for e in entries} == {"idna"}

    def test_neutral_noarch_bundle_matches_every_config_and_link(self):
        """A none/none (config/link-neutral) noarch bundle serves every variant."""
        cat = {
            "bundles": [
                {**self.NOARCH_CATALOG["bundles"][0], "build_type": "none", "link": "none"},
                self.NOARCH_CATALOG["bundles"][1],
            ]
        }
        for cfg, lnk in (("release", "shared"), ("debug", "static")):
            entries = catalog_entries(
                cat, platform="linux", arch="x86_64", build_type=cfg, link=lnk
            )
            names = {e.name for e in entries}
            assert "idna" in names
            # The concrete release/shared bundle still filters exactly.
            assert ("numpy-cp311" in names) == ((cfg, lnk) == ("release", "shared"))

    # ...and a noarch bundle compiles nothing, so it serves every config and
    # link mode: a static request (wasm is static-only) or a debug one must
    # still find the one release/shared bundle a noarch recipe publishes.

    def test_noarch_matches_a_static_request(self):
        entries = catalog_entries(
            self.NOARCH_CATALOG,
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="static",
        )
        # numpy-cp311 is concrete and shared, so a static request still skips it.
        assert {e.name for e in entries} == {"idna"}

    def test_noarch_matches_a_wasm_request(self):
        entries = catalog_entries(
            self.NOARCH_CATALOG, platform="wasm", arch="wasm32", build_type="release", link="static"
        )
        assert [(e.name, e.link) for e in entries] == [("idna", "shared")]

    def test_noarch_matches_a_debug_request(self):
        entries = catalog_entries(
            self.NOARCH_CATALOG, platform="linux", arch="x86_64", build_type="debug", link="shared"
        )
        assert {e.name for e in entries} == {"idna"}

    def test_noarch_by_arch_alone_matches_any_link(self):
        cat = {"bundles": [dict(self.NOARCH_CATALOG["bundles"][0], platform="linux")]}
        entries = catalog_entries(cat, platform="linux", arch="x86_64", link="static")
        assert [e.name for e in entries] == ["idna"]

    def test_noarch_keeps_the_exact_variant_when_there_are_several(self):
        idna = self.NOARCH_CATALOG["bundles"][0]
        cat = {
            "bundles": [
                idna,
                dict(idna, link="static", sha256="1" * 64, archive_url="https://e/s.tar.gz"),
                dict(idna, build_type="debug", sha256="2" * 64, archive_url="https://e/d.tar.gz"),
            ]
        }
        static = catalog_entries(
            cat, platform="wasm", arch="wasm32", build_type="release", link="static"
        )
        assert [(e.build_type, e.link) for e in static] == [("release", "static")]
        shared = catalog_entries(
            cat, platform="linux", arch="x86_64", build_type="release", link="shared"
        )
        assert [(e.build_type, e.link) for e in shared] == [("release", "shared")]
        # Debug/static matches no variant exactly: the nearest ones (one of the two) stay, the
        # release/shared one (neither) goes.
        near = catalog_entries(
            cat, platform="linux", arch="x86_64", build_type="debug", link="static"
        )
        assert sorted((e.build_type, e.link) for e in near) == [
            ("debug", "shared"),
            ("release", "static"),
        ]

    def test_noarch_variants_of_different_versions_are_kept(self):
        idna = self.NOARCH_CATALOG["bundles"][0]
        cat = {"bundles": [idna, dict(idna, version="3.17+cvc.1", link="static", sha256="3" * 64)]}
        entries = catalog_entries(cat, platform="linux", arch="x86_64", link="shared")
        assert sorted(e.version for e in entries) == ["3.16+cvc.1", "3.17+cvc.1"]


# ── generate_catalog ────────────────────────────────────────────


def _write_index(path, bundles):
    """Helper: write an index YAML with the given bundle list."""
    path.write_text(yaml.dump({"bundles": bundles}, default_flow_style=False))


class TestGenerateCatalog:
    """Tests for the generate_catalog function."""

    def _make_bundle(
        self,
        name="zlib",
        version="1.3.1",
        platform="linux",
        archive="zlib-1.3.1-linux-x86_64-release.tar.gz",
        **extra,
    ):
        bundle = {
            "name": name,
            "version": version,
            "platform": platform,
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
            "sha256": "a" * 64,
            "archive": archive,
        }
        bundle.update(extra)
        return bundle

    def test_basic_generation(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "linux-release-shared-index.yaml",
            [
                self._make_bundle(),
            ],
        )

        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")

        assert cat["schema_version"] == 1
        assert cat["revision"] == 1
        assert len(cat["bundles"]) == 1
        assert cat["bundles"][0]["source_release"] == "v1.0.0"
        assert (
            cat["bundles"][0]["archive_url"]
            == "https://cvcpkg.org/v1/download/zlib-1.3.1-linux-x86_64-release.tar.gz"
        )

    def test_output_files_created(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "linux-release-shared-index.yaml",
            [
                self._make_bundle(),
            ],
        )

        generate_catalog(idx_dir, out_dir, release_tag="v2.0.0")

        assert (out_dir / "latest.yaml").exists()
        assert (out_dir / "1.yaml").exists()
        assert (out_dir / "index.yaml").exists()
        assert (out_dir / "v2.0.0-index.yaml").exists()

    def test_latest_yaml_content(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(idx_dir / "test-index.yaml", [self._make_bundle()])
        generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")

        latest = yaml.safe_load((out_dir / "latest.yaml").read_text())
        assert latest["schema_version"] == 1
        assert latest["revision"] == 1
        assert len(latest["bundles"]) == 1

    def test_revision_yaml_matches_latest(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(idx_dir / "test-index.yaml", [self._make_bundle()])
        generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")

        latest_text = (out_dir / "latest.yaml").read_text()
        rev_text = (out_dir / "1.yaml").read_text()
        assert latest_text == rev_text

    def test_index_manifest(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(idx_dir / "test-index.yaml", [self._make_bundle()])
        generate_catalog(idx_dir, out_dir, release_tag="v3.0.0")

        manifest = yaml.safe_load((out_dir / "index.yaml").read_text())
        assert manifest["latest_revision"] == 1
        assert len(manifest["revisions"]) == 1
        assert manifest["revisions"][0]["revision"] == 1
        assert manifest["revisions"][0]["release"] == "v3.0.0"
        # Verify SHA-256 matches actual catalog content
        latest_text = (out_dir / "latest.yaml").read_text()
        expected_sha = hashlib.sha256(latest_text.encode()).hexdigest()
        assert manifest["revisions"][0]["sha256"] == expected_sha

    def test_release_index(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "test-index.yaml",
            [
                self._make_bundle("zlib", "1.3.1"),
                self._make_bundle("boost", "1.86.0", archive="boost-1.86.0.tar.gz"),
            ],
        )
        generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")

        rel_idx = yaml.safe_load((out_dir / "v1.0.0-index.yaml").read_text())
        assert rel_idx["schema_version"] == 1
        assert rel_idx["release_version"] == "1.0.0"  # stripped 'v' prefix
        assert rel_idx["recommended"]["zlib"] == "1.3.1"
        assert rel_idx["recommended"]["boost"] == "1.86.0"
        assert len(rel_idx["bundles"]) == 2

    def test_multiple_index_files_merged(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "linux-release-shared-index.yaml",
            [
                self._make_bundle("zlib", "1.3.1"),
            ],
        )
        _write_index(
            idx_dir / "windows-release-shared-index.yaml",
            [
                self._make_bundle(
                    "zlib", "1.3.1", platform="windows", archive="zlib-1.3.1-windows.zip"
                ),
            ],
        )

        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        assert len(cat["bundles"]) == 2

    def test_custom_server_url(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(idx_dir / "test-index.yaml", [self._make_bundle()])
        cat = generate_catalog(
            idx_dir,
            out_dir,
            release_tag="v1.0.0",
            server_url="https://custom.example.com",
        )
        assert cat["bundles"][0]["archive_url"].startswith("https://custom.example.com/")

    def test_base_revision(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(idx_dir / "test-index.yaml", [self._make_bundle()])
        cat = generate_catalog(
            idx_dir,
            out_dir,
            release_tag="v2.0.0",
            base_revision=5,
        )
        assert cat["revision"] == 6
        assert (out_dir / "6.yaml").exists()

    def test_existing_archive_url_preserved(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "test-index.yaml",
            [
                self._make_bundle(archive_url="https://mirror.example.com/zlib.tar.gz"),
            ],
        )
        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        # Pre-existing archive_url should not be overwritten
        assert cat["bundles"][0]["archive_url"] == "https://mirror.example.com/zlib.tar.gz"

    def test_mirror_urls_removed(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "test-index.yaml",
            [
                self._make_bundle(mirror_urls=["https://old-mirror.example.com/zlib.tar.gz"]),
            ],
        )
        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        assert "mirror_urls" not in cat["bundles"][0]

    def test_empty_indexes_dir(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        assert cat["bundles"] == []
        assert cat["revision"] == 1
        assert (out_dir / "latest.yaml").exists()

    def test_bundle_without_archive_field(self, tmp_path):
        """Bundles without an 'archive' field should not get archive_url set."""
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        bundle = {
            "name": "meta-pkg",
            "version": "1.0.0",
            "platform": "linux",
        }
        _write_index(idx_dir / "test-index.yaml", [bundle])
        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        assert "archive_url" not in cat["bundles"][0]
        assert cat["bundles"][0]["source_release"] == "v1.0.0"

    def test_output_dir_created_if_missing(self, tmp_path):
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "deeply" / "nested" / "output"

        _write_index(idx_dir / "test-index.yaml", [self._make_bundle()])
        generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        assert out_dir.exists()
        assert (out_dir / "latest.yaml").exists()

    def test_non_index_yaml_files_ignored(self, tmp_path):
        """Only *-index.yaml files should be read, not other YAML files."""
        idx_dir = tmp_path / "indexes"
        idx_dir.mkdir()
        out_dir = tmp_path / "output"

        _write_index(
            idx_dir / "linux-release-shared-index.yaml",
            [
                self._make_bundle(),
            ],
        )
        # This file should be ignored (doesn't match *-index.yaml)
        (idx_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "bundles": [
                        {"name": "rogue", "version": "9.9.9", "platform": "linux"},
                    ]
                }
            )
        )

        cat = generate_catalog(idx_dir, out_dir, release_tag="v1.0.0")
        names = [b["name"] for b in cat["bundles"]]
        assert "rogue" not in names
        assert len(cat["bundles"]) == 1
