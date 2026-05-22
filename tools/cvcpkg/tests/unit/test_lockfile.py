"""Tests for cvcpkg.lockfile — read/write lockfile.yaml."""

from __future__ import annotations

import yaml

from cvcpkg.lockfile import LockEntry, Lockfile


class TestLockfile:
    def test_roundtrip_dict(self):
        lf = Lockfile(
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
            catalog_revision=42,
            catalog_sha256="d" * 64,
            bundles=[
                LockEntry(name="zlib", version="1.3.1+cvc.1", sha256="a" * 64, size_bytes=100),
                LockEntry(name="boost", version="1.86.0+cvc.1"),
            ],
        )
        d = lf.to_dict()
        assert d["schema_version"] == 2
        assert d["platform"] == "linux"
        assert len(d["bundles"]) == 2
        assert d["bundles"][0]["name"] == "zlib"
        assert d["bundles"][0]["sha256"] == "a" * 64

        # Round-trip
        lf2 = Lockfile.from_dict(d)
        assert lf2.platform == lf.platform
        assert lf2.catalog_revision == 42
        assert len(lf2.bundles) == 2
        assert lf2.bundles[0].sha256 == "a" * 64

    def test_write_and_read(self, tmp_path):
        path = tmp_path / "share" / "libcvc-deps" / "lockfile.yaml"
        lf = Lockfile(
            platform="macos",
            arch="arm64",
            config="debug",
            link="static",
            bundles=[LockEntry(name="qt6", version="6.8.2+cvc.1")],
        )
        lf.write(path)

        assert path.exists()
        raw = yaml.safe_load(path.read_text())
        assert raw["platform"] == "macos"

        lf2 = Lockfile.read(path)
        assert lf2.arch == "arm64"
        assert lf2.config == "debug"
        assert lf2.bundles[0].name == "qt6"

    def test_resolved_at_auto_set(self):
        lf = Lockfile()
        d = lf.to_dict()
        assert d["resolved_at"]  # auto-populated if empty

    def test_resolved_at_preserved(self):
        lf = Lockfile(resolved_at="2026-05-22T00:00:00Z")
        d = lf.to_dict()
        assert d["resolved_at"] == "2026-05-22T00:00:00Z"

    def test_empty_lockfile(self):
        lf = Lockfile.from_dict({})
        assert lf.bundles == []
        assert lf.platform == ""

    def test_defaults(self):
        lf = Lockfile()
        assert lf.schema_version == 2
        assert lf.bundles == []
        assert lf.catalog_revision == 0
