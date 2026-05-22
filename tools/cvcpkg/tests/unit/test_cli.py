"""Tests for cvcpkg.cli — smoke tests and new command coverage."""

import os
from pathlib import Path
from unittest import mock

import pytest
import yaml

from cvcpkg.cli import main


def test_help_returns_zero():
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0


def test_version(capsys):
    try:
        main(["--version"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "cvcpkg" in captured.out


def test_install_no_components(capsys):
    ret = main(["install"])
    captured = capsys.readouterr()
    assert "nothing to do" in captured.out.lower() or ret == 0


def test_validate_components():
    """Validate components.yaml from the repo."""
    if os.path.exists("packaging/components.yaml"):
        ret = main(["validate", "components"])
        assert ret == 0


# ── CLI subcommand help ─────────────────────────────────────────

@pytest.mark.parametrize("subcmd", [
    "install", "list", "info", "validate", "verify",
    "lock", "sync", "catalog", "gc",
    "build", "pack", "recipes",
])
def test_subcommand_help(subcmd):
    with pytest.raises(SystemExit) as exc_info:
        main([subcmd, "--help"])
    assert exc_info.value.code == 0


# ── recipes command ─────────────────────────────────────────────

def test_recipes_list(capsys):
    """cvcpkg recipes --list should print the recipe table."""
    ret = main(["recipes", "--list"])
    captured = capsys.readouterr()
    # Should list at least zlib
    assert ret == 0
    assert "zlib" in captured.out

def test_recipes_show(capsys):
    """cvcpkg recipes --show zlib should print recipe details."""
    ret = main(["recipes", "--show", "zlib"])
    captured = capsys.readouterr()
    assert ret == 0
    assert "zlib" in captured.out
    assert "Version:" in captured.out or "1.3.1" in captured.out

def test_recipes_show_not_found(capsys):
    ret = main(["recipes", "--show", "nonexistent-pkg-xyz"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out

def test_recipes_default_is_list(capsys):
    """Plain 'cvcpkg recipes' should default to --list behavior."""
    ret = main(["recipes"])
    captured = capsys.readouterr()
    assert ret == 0
    assert "zlib" in captured.out


# ── build / pack argument parsing ──────────────────────────────

def test_build_no_recipe(capsys):
    """cvcpkg build without recipe should print usage."""
    with pytest.raises(SystemExit) as exc_info:
        main(["build"])
    assert exc_info.value.code != 0

def test_pack_no_recipe(capsys):
    """cvcpkg pack without recipe should print usage."""
    with pytest.raises(SystemExit) as exc_info:
        main(["pack"])
    assert exc_info.value.code != 0


# ── unknown command ─────────────────────────────────────────────

def test_unknown_command():
    with pytest.raises(SystemExit) as exc_info:
        main(["frobnicate"])
    assert exc_info.value.code == 2  # argparse rejects invalid choices


# ── no command ──────────────────────────────────────────────────

def test_no_command(capsys):
    ret = main([])
    assert ret == 0


# ── Fixture: mock catalog for install / list --available / info ──

def _make_catalog(tmp_path: Path) -> Path:
    """Create a minimal catalog YAML for testing the consumer-side CLI."""
    catalog = {
        "schema_version": 1,
        "revision": 1,
        "bundles": [
            {
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "upstream_version": "1.3.1",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "abc123",
                "size_bytes": 100000,
                "archive_url": "",
                "source_release": "v1.1.0",
            },
            {
                "name": "yaml",
                "version": "0.2.5+cvc.1",
                "upstream_version": "0.2.5",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "def456",
                "size_bytes": 50000,
                "archive_url": "",
                "source_release": "v1.1.0",
            },
        ],
    }
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.dump(catalog, default_flow_style=False))
    return p


class TestInstallWithCatalog:
    """Test install against a local catalog file."""

    def test_install_resolves_components(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main([
                "install", "zlib", "yaml",
                "--catalog", str(cat),
                "--prefix", str(prefix),
                "--platform", "linux",
                "--arch", "x86_64",
            ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "resolved 2" in out
        assert "zlib" in out
        assert "yaml" in out

    def test_install_writes_lockfile(self, tmp_path):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            main([
                "install", "zlib",
                "--catalog", str(cat),
                "--prefix", str(prefix),
                "--platform", "linux",
                "--arch", "x86_64",
            ])
        lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
        assert lock_path.exists()
        lock_data = yaml.safe_load(lock_path.read_text())
        assert len(lock_data["bundles"]) == 1
        assert lock_data["bundles"][0]["name"] == "zlib"

    def test_install_from_requirements_file(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text(yaml.dump({
            "platform": "linux",
            "arch": "x86_64",
            "config": "release",
            "link": "shared",
            "components": ["zlib"],
        }))
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main([
                "install",
                "--from", str(req_file),
                "--catalog", str(cat),
                "--prefix", str(prefix),
            ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out

    def test_install_no_catalog_match(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        ret = main([
            "install", "zlib",
            "--catalog", str(cat),
            "--prefix", str(prefix),
            "--platform", "windows",
            "--arch", "x86_64",
        ])
        assert ret == 1


class TestListAvailable:
    def test_list_available_shows_components(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        with mock.patch("cvcpkg.catalog.fetch_catalog") as m:
            m.return_value = yaml.safe_load(cat.read_text())
            ret = main(["list", "--available"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out
        assert "yaml" in out


class TestInfoCommand:
    def test_info_found(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        with mock.patch("cvcpkg.catalog.fetch_catalog") as m:
            m.return_value = yaml.safe_load(cat.read_text())
            ret = main(["info", "zlib"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out
        assert "1.3.1" in out

    def test_info_not_found(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        with mock.patch("cvcpkg.catalog.fetch_catalog") as m:
            m.return_value = yaml.safe_load(cat.read_text())
            ret = main(["info", "nonexistent"])
        assert ret == 1


class TestVerifyCommand:
    def test_verify_no_lockfile(self, tmp_path, capsys):
        prefix = tmp_path / "empty"
        ret = main(["verify", "--prefix", str(prefix)])
        assert ret == 1

    def test_verify_ok(self, tmp_path, capsys):
        from cvcpkg.lockfile import LockEntry, Lockfile
        from cvcpkg.manifest import BundleManifest

        prefix = tmp_path / "prefix"
        # Write lockfile
        lock = Lockfile(
            platform="linux", arch="x86_64", config="release", link="shared",
            bundles=[LockEntry(name="zlib", version="1.3.1+cvc.1", upstream_version="1.3.1")],
        )
        lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
        lock.write(lock_path)

        # Write a matching manifest
        mdir = prefix / "share" / "libcvc-deps" / "zlib"
        mdir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 3,
            "bundle": {
                "name": "zlib", "version": "1.3.1+cvc.1",
                "upstream_version": "1.3.1", "cvc_revision": 1,
                "platform": "linux", "arch": "x86_64",
                "build_type": "release", "link": "shared",
            },
            "contents": {"files": []},
        }
        (mdir / "manifest.yaml").write_text(yaml.dump(manifest))

        ret = main(["verify", "--prefix", str(prefix)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "OK" in out

    def test_verify_version_mismatch(self, tmp_path, capsys):
        from cvcpkg.lockfile import LockEntry, Lockfile

        prefix = tmp_path / "prefix"
        lock = Lockfile(
            platform="linux", arch="x86_64", config="release", link="shared",
            bundles=[LockEntry(name="zlib", version="1.3.1+cvc.2", upstream_version="1.3.1")],
        )
        lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")
        mdir = prefix / "share" / "libcvc-deps" / "zlib"
        mdir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 3,
            "bundle": {
                "name": "zlib", "version": "1.3.1+cvc.1",
                "upstream_version": "1.3.1", "cvc_revision": 1,
                "platform": "linux", "arch": "x86_64",
                "build_type": "release", "link": "shared",
            },
            "contents": {"files": []},
        }
        (mdir / "manifest.yaml").write_text(yaml.dump(manifest))

        ret = main(["verify", "--prefix", str(prefix)])
        assert ret == 1
        out = capsys.readouterr().out
        assert "MISMATCH" in out


class TestSyncCommand:
    def test_sync_no_lockfile(self, tmp_path, capsys):
        ret = main(["sync", "--prefix", str(tmp_path / "empty")])
        assert ret == 1

    def test_sync_already_in_sync(self, tmp_path, capsys):
        from cvcpkg.lockfile import LockEntry, Lockfile

        prefix = tmp_path / "prefix"
        lock = Lockfile(
            platform="linux", arch="x86_64", config="release", link="shared",
            bundles=[LockEntry(name="zlib", version="1.3.1+cvc.1")],
        )
        lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")
        # Create manifest so it looks installed
        mdir = prefix / "share" / "libcvc-deps" / "zlib"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "manifest.yaml").write_text("schema_version: 3")

        ret = main(["sync", "--prefix", str(prefix)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "in sync" in out


class TestLockCommand:
    def test_lock_shows_guidance(self, capsys):
        ret = main(["lock"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "lockfile" in out.lower()


class TestCatalogCommand:
    def test_catalog_default(self, capsys):
        ret = main(["catalog"])
        assert ret == 0

    def test_catalog_show(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        with mock.patch("cvcpkg.catalog.fetch_catalog") as m:
            m.return_value = yaml.safe_load(cat.read_text())
            ret = main(["catalog", "--show"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "revision" in out.lower()

    def test_catalog_refresh(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        with mock.patch("cvcpkg.catalog.fetch_catalog") as m:
            m.return_value = yaml.safe_load(cat.read_text())
            ret = main(["catalog", "--refresh"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "refreshed" in out.lower()
