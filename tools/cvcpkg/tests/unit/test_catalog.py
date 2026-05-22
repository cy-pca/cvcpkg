"""Tests for cvcpkg.catalog — fetch, cache, and filter catalog entries."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.catalog import catalog_entries, load_catalog_from_file
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
