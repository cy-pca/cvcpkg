"""Tests for cvcpkg.cli — smoke tests and new command coverage."""

import os
from pathlib import Path
from unittest import mock

import click
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


@pytest.mark.parametrize(
    "subcmd",
    [
        "install",
        "list",
        "info",
        "validate",
        "verify",
        "lock",
        "sync",
        "catalog",
        "gc",
        "clean",
        "build",
        "pack",
        "publish",
        "recipes",
    ],
)
def test_subcommand_help(subcmd):
    ret = main([subcmd, "--help"])
    assert ret == 0


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
    assert "not found" in captured.out or "not found" in captured.err


def test_recipes_default_is_list(capsys):
    """Plain 'cvcpkg recipes' should default to --list behavior."""
    ret = main(["recipes"])
    captured = capsys.readouterr()
    assert ret == 0
    assert "zlib" in captured.out


# ── build / pack argument parsing ──────────────────────────────


def test_build_no_recipe(capsys):
    """cvcpkg build without recipe should error."""
    ret = main(["build"])
    assert ret != 0


def test_pack_no_recipe(capsys):
    """cvcpkg pack without recipe should error."""
    ret = main(["pack"])
    assert ret != 0


# ── unknown command ─────────────────────────────────────────────


def test_unknown_command():
    ret = main(["frobnicate"])
    assert ret != 0


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
            ret = main(
                [
                    "install",
                    "zlib",
                    "yaml",
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                ]
            )
        assert ret == 0


# ── Hardening: invalid platform/arch Choice validation ──────────


class TestPlatformArchValidation:
    """Verify click.Choice rejects invalid --platform and --arch values."""

    def test_invalid_platform_rejected(self, capsys):
        """An invalid --platform value should cause a non-zero exit."""
        ret = main(["install", "zlib", "--platform", "solaris"])
        assert ret != 0

    def test_invalid_arch_rejected(self, capsys):
        """An invalid --arch value should cause a non-zero exit."""
        ret = main(["install", "zlib", "--arch", "mips64"])
        assert ret != 0

    def test_valid_platform_accepted(self, capsys):
        """Valid platform values should not trigger a Choice error."""
        for plat in ("auto", "linux", "macos", "windows"):
            ret = main(["install", "--platform", plat])
            assert ret == 0 or ret is None

    def test_valid_arch_accepted(self, capsys):
        """Valid arch values should not trigger a Choice error."""
        for arch in ("auto", "x86_64", "arm64"):
            ret = main(["install", "--arch", arch])
            assert ret == 0 or ret is None

    def test_invalid_config_rejected(self, capsys):
        """An invalid --config value should cause a non-zero exit."""
        ret = main(["install", "zlib", "--config", "optimized"])
        assert ret != 0

    def test_invalid_link_rejected(self, capsys):
        """An invalid --link value should cause a non-zero exit."""
        ret = main(["install", "zlib", "--link", "dynamic"])
        assert ret != 0

    def test_install_writes_lockfile(self, tmp_path):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            main(
                [
                    "install",
                    "zlib",
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                ]
            )
        lock_path = prefix / "share" / "libcvc-deps" / "lockfile.yaml"
        assert lock_path.exists()
        lock_data = yaml.safe_load(lock_path.read_text())
        assert len(lock_data["bundles"]) == 1
        assert lock_data["bundles"][0]["name"] == "zlib"

    def test_install_from_requirements_file(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text(
            yaml.dump(
                {
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "components": ["zlib"],
                }
            )
        )
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "--from",
                    str(req_file),
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                ]
            )
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out

    def test_install_config_overrides_requirements(self, tmp_path, capsys):
        """--config on CLI overrides config in requirements file."""
        # Create a catalog with a debug entry
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
                    "build_type": "debug",
                    "link": "shared",
                    "sha256": "abc123",
                    "size_bytes": 100000,
                    "archive_url": "",
                    "source_release": "v1.1.0",
                },
            ],
        }
        cat = tmp_path / "catalog-debug.yaml"
        cat.write_text(yaml.dump(catalog, default_flow_style=False))

        prefix = tmp_path / "prefix"
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text(
            yaml.dump(
                {
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "components": ["zlib"],
                }
            )
        )
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "--from",
                    str(req_file),
                    "--config",
                    "debug",
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                ]
            )
        assert ret == 0
        out = capsys.readouterr().out
        assert "debug" in out
        assert "zlib" in out

    def test_install_link_overrides_requirements(self, tmp_path, capsys):
        """--link on CLI overrides link in requirements file."""
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
                    "link": "static",
                    "sha256": "abc123",
                    "size_bytes": 100000,
                    "archive_url": "",
                    "source_release": "v1.1.0",
                },
            ],
        }
        cat = tmp_path / "catalog-static.yaml"
        cat.write_text(yaml.dump(catalog, default_flow_style=False))

        prefix = tmp_path / "prefix"
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text(
            yaml.dump(
                {
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "components": ["zlib"],
                }
            )
        )
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "--from",
                    str(req_file),
                    "--link",
                    "static",
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                ]
            )
        assert ret == 0
        out = capsys.readouterr().out
        assert "static" in out
        assert "zlib" in out

    def test_install_no_catalog_match(self, tmp_path, capsys):
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        ret = main(
            [
                "install",
                "zlib",
                "--catalog",
                str(cat),
                "--prefix",
                str(prefix),
                "--platform",
                "windows",
                "--arch",
                "x86_64",
            ]
        )
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

        prefix = tmp_path / "prefix"
        # Write lockfile
        lock = Lockfile(
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
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
                "name": "zlib",
                "version": "1.3.1+cvc.1",
                "upstream_version": "1.3.1",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
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
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
            bundles=[LockEntry(name="zlib", version="1.3.1+cvc.2", upstream_version="1.3.1")],
        )
        lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")
        mdir = prefix / "share" / "libcvc-deps" / "zlib"
        mdir.mkdir(parents=True, exist_ok=True)
        manifest = {
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
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
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


# ── recipes --tag filtering ─────────────────────────────────────


class TestRecipesTagFilter:
    def test_tag_filter_shows_matching(self, capsys):
        """'cvcpkg recipes --tag math' should show only math recipes."""
        ret = main(["recipes", "--tag", "math"])
        assert ret == 0
        out = capsys.readouterr().out
        # At least fftw3, gsl, openblas should appear
        assert "fftw3" in out
        assert "gsl" in out
        # zlib is tagged utils/io, not math
        assert "zlib" not in out.split("Name")[1] if "Name" in out else True

    def test_tag_filter_no_match(self, capsys):
        """Non-existent tag should fail."""
        ret = main(["recipes", "--tag", "nonexistent-tag-xyz"])
        assert ret == 1

    def test_recipes_show_displays_tags(self, capsys):
        """'cvcpkg recipes --show zlib' should display tags."""
        ret = main(["recipes", "--show", "zlib"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Tags:" in out
        assert "utils" in out

    def test_recipes_list_shows_tags_column(self, capsys):
        """Default list should include a Tags column."""
        ret = main(["recipes"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Tags" in out


# ── multi --recipes-dir ─────────────────────────────────────────


class TestMultiRecipesDir:
    def _make_recipe_dir(self, base, name, tags=None, revision=1):
        """Create a minimal recipe directory."""
        rdir = base / name
        rdir.mkdir(parents=True, exist_ok=True)
        recipe = {
            "schema_version": 1,
            "recipe": {
                "name": name,
                "upstream_version": "1.0.0",
                "cvc_revision": revision,
            },
            "source": {"type": "vendored", "path": f"third-party/{name}"},
            "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            "package": {"files": ["lib/*"], "cmake_packages": []},
        }
        if tags:
            recipe["recipe"]["tags"] = tags
        (rdir / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))
        (rdir / "build.sh").write_text("#!/bin/bash\ntrue\n")

    def test_custom_recipes_dir(self, tmp_path, capsys):
        """'cvcpkg recipes --recipes-dir <dir>' uses that directory."""
        self._make_recipe_dir(tmp_path, "mypkg", tags=["custom"])
        ret = main(["recipes", "--recipes-dir", str(tmp_path)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "mypkg" in out

    def test_multiple_recipes_dirs(self, tmp_path, capsys):
        """Multiple --recipes-dir flags merge recipes."""
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        self._make_recipe_dir(d1, "alpha")
        self._make_recipe_dir(d2, "beta")
        ret = main(["recipes", "--recipes-dir", str(d1), "--recipes-dir", str(d2)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_override_recipe_warning(self, tmp_path, capsys):
        """Later --recipes-dir overrides earlier on name conflict."""
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        self._make_recipe_dir(d1, "alpha", revision=1)
        self._make_recipe_dir(d2, "alpha", revision=2)
        ret = main(["recipes", "--recipes-dir", str(d1), "--recipes-dir", str(d2)])
        assert ret == 0
        out = capsys.readouterr().out
        # Should show the version from d2 (cvc_revision=2)
        assert "1.0.0+cvc.2" in out


# ── add / remove commands ───────────────────────────────────────


class TestAddCommand:
    def test_add_component(self, tmp_path, capsys):
        req = tmp_path / "req.yaml"
        req.write_text(yaml.dump({"components": ["zlib"]}))
        ret = main(["add", "boost", "--from", str(req)])
        assert ret == 0
        data = yaml.safe_load(req.read_text())
        names = [c if isinstance(c, str) else c.get("name", "") for c in data["components"]]
        assert "boost" in names
        assert "zlib" in names

    def test_add_duplicate_skipped(self, tmp_path, capsys):
        req = tmp_path / "req.yaml"
        req.write_text(yaml.dump({"components": ["zlib"]}))
        ret = main(["add", "zlib", "--from", str(req)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "already" in out or "nothing" in out

    def test_add_with_version(self, tmp_path, capsys):
        req = tmp_path / "req.yaml"
        req.write_text(yaml.dump({"components": []}))
        ret = main(["add", "hdf5==1.14.5+cvc.1", "--from", str(req)])
        assert ret == 0
        data = yaml.safe_load(req.read_text())
        assert any(isinstance(c, dict) and c.get("name") == "hdf5" for c in data["components"])


class TestRemoveCommand:
    def test_remove_component(self, tmp_path, capsys):
        req = tmp_path / "req.yaml"
        req.write_text(yaml.dump({"components": ["zlib", "boost"]}))
        ret = main(["remove", "boost", "--from", str(req)])
        assert ret == 0
        data = yaml.safe_load(req.read_text())
        names = [c if isinstance(c, str) else c.get("name", "") for c in data["components"]]
        assert "boost" not in names
        assert "zlib" in names

    def test_remove_nonexistent(self, tmp_path, capsys):
        req = tmp_path / "req.yaml"
        req.write_text(yaml.dump({"components": ["zlib"]}))
        ret = main(["remove", "nonexistent", "--from", str(req)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "none of" in out.lower()


# ── publish --dest (storage backend) ────────────────────────────


class TestPublishDest:
    def test_publish_dest_file_backend(self, tmp_path, capsys):
        """Publish an archive to a file:// destination."""
        archive = tmp_path / "test-1.0.tar.gz"
        archive.write_bytes(b"fake archive data")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        ret = main(["publish", str(archive), "--dest", f"file://{dest_dir}"])
        assert ret == 0
        assert (dest_dir / "test-1.0.tar.gz").exists()
        assert (dest_dir / "test-1.0.tar.gz").read_bytes() == b"fake archive data"

    def test_publish_dest_missing_file(self, tmp_path, capsys):
        ret = main(
            ["publish", str(tmp_path / "nonexistent.tar.gz"), "--dest", f"file://{tmp_path}"]
        )
        assert ret == 1

    def test_publish_requires_server_or_dest(self, capsys):
        """Error when neither --server nor --dest is given."""
        ret = main(["publish", "zlib"])
        assert ret != 0

    def test_publish_server_and_dest_exclusive(self, capsys):
        """Error when both --server and --dest are given."""
        ret = main(
            [
                "publish",
                "zlib",
                "--server",
                "https://fake.example.com",
                "--token",
                "tok",
                "--dest",
                "file:///tmp",
            ]
        )
        assert ret != 0

    def test_publish_server_requires_token(self, capsys):
        """Error when --server is given without --token."""
        ret = main(["publish", "zlib", "--server", "https://fake.example.com"])
        assert ret != 0


# ── world command ───────────────────────────────────────────────


class TestWorldCommand:
    def test_world_help(self, capsys):
        ret = main(["world", "--help"])
        assert ret == 0

    def test_world_empty_requirements(self, tmp_path, capsys):
        req = tmp_path / "req.yaml"
        req.write_text(yaml.dump({"components": []}))
        ret = main(["world", "--from", str(req), "--prefix", str(tmp_path / "pfx")])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no" in out.lower()


# ── new subcommand help coverage ────────────────────────────────


@pytest.mark.parametrize("subcmd", ["add", "remove", "world", "clean"])
def test_new_subcommand_help(subcmd, capsys):
    ret = main([subcmd, "--help"])
    assert ret == 0


# ── cross-platform (wasm) build dependency handling ─────────────


class TestBuildCrossPlatformDeps:
    """Verify that `cvcpkg build --platform wasm --with-deps` builds
    host-tool dependencies (like emsdk) for the native host platform
    before building the wasm target recipe."""

    def _make_recipe(self, recipes_dir, name, *, matrix, deps=None):
        d = recipes_dir / name
        d.mkdir(parents=True)
        recipe = {
            "schema_version": 1,
            "recipe": {"name": name, "upstream_version": "1.0.0", "cvc_revision": 1},
            "source": {"type": "vendored", "path": "."},
            "patches": [],
            "build": {"matrix": matrix},
            "package": {"files": ["lib/*"]},
        }
        if deps:
            recipe["depends"] = {"build": deps}
        (d / "recipe.yaml").write_text(yaml.dump(recipe))
        # Create dummy build scripts referenced by matrix entries
        for m in matrix:
            (d / m["script"]).write_text("#!/bin/sh\ntrue\n")

    def test_host_tool_built_before_wasm_target(self, tmp_path):
        """emsdk-like host tool is built with host platform, then wasm target."""
        recipes_dir = tmp_path / "recipes"
        # emsdk: host-only tool (linux/macos/windows, no wasm)
        self._make_recipe(
            recipes_dir,
            "emsdk",
            matrix=[{"platform": "linux", "script": "build.sh"}],
        )
        # wasmlib: wasm target depending on emsdk
        self._make_recipe(
            recipes_dir,
            "wasmlib",
            matrix=[{"platform": "wasm", "script": "build.sh"}],
            deps=[{"name": "emsdk", "version": ">=1.0"}],
        )

        build_calls = []

        def mock_build_recipe(
            recipe_dir,
            *,
            platform,
            config,
            link,
            prefix,
            keep_build_dir,
            host_platform="",
            cross_toolchain_env=None,
        ):
            build_calls.append((recipe_dir.name, platform))
            # Return a minimal mock context
            return mock.MagicMock()

        with (
            mock.patch("cvcpkg.builder.build_recipe", side_effect=mock_build_recipe),
            mock.patch("cvcpkg.platform.detect_platform", return_value="linux"),
        ):
            ret = main(
                [
                    "build",
                    "wasmlib",
                    "--platform",
                    "wasm",
                    "--prefix",
                    str(tmp_path / "pfx"),
                    "--recipes-dir",
                    str(recipes_dir),
                ]
            )

        assert ret == 0
        assert len(build_calls) == 2
        # emsdk built first, for the host platform
        assert build_calls[0] == ("emsdk", "linux")
        # wasmlib built second, for the wasm target
        assert build_calls[1] == ("wasmlib", "wasm")

    def test_native_build_no_host_tool_split(self, tmp_path):
        """When building for a native platform, no host-tool splitting occurs."""
        recipes_dir = tmp_path / "recipes"
        self._make_recipe(
            recipes_dir,
            "liba",
            matrix=[{"platform": "linux", "script": "build.sh"}],
        )
        self._make_recipe(
            recipes_dir,
            "libb",
            matrix=[{"platform": "linux", "script": "build.sh"}],
            deps=["liba"],
        )

        build_calls = []

        def mock_build_recipe(
            recipe_dir,
            *,
            platform,
            config,
            link,
            prefix,
            keep_build_dir,
            host_platform="",
            cross_toolchain_env=None,
        ):
            build_calls.append((recipe_dir.name, platform))
            return mock.MagicMock()

        with (
            mock.patch("cvcpkg.builder.build_recipe", side_effect=mock_build_recipe),
            mock.patch("cvcpkg.platform.detect_platform", return_value="linux"),
        ):
            ret = main(
                [
                    "build",
                    "libb",
                    "--platform",
                    "linux",
                    "--prefix",
                    str(tmp_path / "pfx"),
                    "--recipes-dir",
                    str(recipes_dir),
                ]
            )

        assert ret == 0
        assert len(build_calls) == 2
        # Both built for linux
        assert build_calls[0] == ("liba", "linux")
        assert build_calls[1] == ("libb", "linux")


# ── --org flag on publish and pack-all ──────────────────────────


class TestPublishOrgFlag:
    """Test that --org option is accepted by the publish command."""

    def test_publish_help_shows_org(self, capsys):
        ret = main(["publish", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--org" in out

    def test_publish_org_flag_accepted(self, capsys):
        """--org should be accepted without error (though publish itself
        will fail without a valid server/token — we test parse only)."""
        _ret = main(["publish", "--help"])
        out = capsys.readouterr().out
        assert "--org" in out
        assert "Organization slug" in out or "organization" in out.lower()


class TestPackAllOrgFlag:
    """Test that --org option is accepted by the pack-all command."""

    def test_pack_all_help_shows_org(self, capsys):
        ret = main(["pack-all", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--org" in out


# ── rev-bump CLI ────────────────────────────────────────────────


class TestRevBumpCli:
    """Tests for the cvcpkg rev-bump CLI command."""

    @staticmethod
    def _make_recipe(recipes_dir, name, deps=None, revision=1):
        d = recipes_dir / name
        d.mkdir(parents=True, exist_ok=True)
        recipe = {
            "schema_version": 1,
            "recipe": {
                "name": name,
                "upstream_version": "1.0.0",
                "cvc_revision": revision,
            },
            "source": {"type": "vendored", "path": f"third-party/{name}"},
            "patches": [],
            "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            "package": {"files": ["lib/*"], "cmake_packages": []},
        }
        if deps:
            recipe["depends"] = {"build": deps}
        (d / "recipe.yaml").write_text(yaml.dump(recipe, default_flow_style=False))

    def test_help(self, capsys):
        ret = main(["rev-bump", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cvc_revision" in out
        assert "--no-cascade" in out

    def test_bump_single(self, tmp_path, capsys):
        recipes_dir = tmp_path / "recipes"
        self._make_recipe(recipes_dir, "alpha")
        self._make_recipe(recipes_dir, "beta", deps=["alpha"])

        ret = main(
            [
                "rev-bump",
                "alpha",
                "--no-cascade",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "alpha: cvc_revision 1" in out
        assert "1 recipe(s) bumped" in out

    def test_bump_with_cascade(self, tmp_path, capsys):
        recipes_dir = tmp_path / "recipes"
        self._make_recipe(recipes_dir, "alpha")
        self._make_recipe(recipes_dir, "beta", deps=["alpha"])
        self._make_recipe(recipes_dir, "gamma", deps=["beta"])

        ret = main(
            [
                "rev-bump",
                "alpha",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out
        assert "gamma" in out
        assert "3 recipe(s) bumped" in out

    def test_bump_nonexistent(self, tmp_path, capsys):
        recipes_dir = tmp_path / "recipes"
        self._make_recipe(recipes_dir, "alpha")

        ret = main(
            [
                "rev-bump",
                "nope",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
        assert ret == 1

    def test_bump_updates_yaml(self, tmp_path):
        recipes_dir = tmp_path / "recipes"
        self._make_recipe(recipes_dir, "a", revision=3)
        self._make_recipe(recipes_dir, "b", deps=["a"], revision=7)

        main(
            [
                "rev-bump",
                "a",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )

        a_yaml = yaml.safe_load((recipes_dir / "a" / "recipe.yaml").read_text())
        b_yaml = yaml.safe_load((recipes_dir / "b" / "recipe.yaml").read_text())
        assert a_yaml["recipe"]["cvc_revision"] == 4
        assert b_yaml["recipe"]["cvc_revision"] == 8


# ── clean command ───────────────────────────────────────────────


class TestCleanCommand:
    """Tests for 'cvcpkg clean'."""

    def _populate(self, d, names, age_minutes=0):
        import time

        for name in names:
            p = d / name
            p.mkdir()
            (p / "build").mkdir()
            (p / "dummy.txt").write_text("x" * 100)
            if age_minutes:
                old = time.time() - age_minutes * 60
                os.utime(p, (old, old))

    def test_clean_empty_dir(self, tmp_path, capsys):
        ret = main(["clean", "--work-dir", str(tmp_path)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no stale" in out

    def test_clean_removes_old_dirs(self, tmp_path, capsys):
        self._populate(tmp_path, ["cvcpkg-zlib-abc123", "cvcpkg-grpc-def456"], age_minutes=180)
        # Also create a non-cvcpkg dir that should be untouched
        (tmp_path / "other-dir").mkdir()
        ret = main(["clean", "--work-dir", str(tmp_path)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "removed" in out
        assert "2 directories" in out
        assert not (tmp_path / "cvcpkg-zlib-abc123").exists()
        assert not (tmp_path / "cvcpkg-grpc-def456").exists()
        assert (tmp_path / "other-dir").exists()

    def test_clean_skips_recent(self, tmp_path, capsys):
        self._populate(tmp_path, ["cvcpkg-new-xyz"], age_minutes=0)
        ret = main(["clean", "--work-dir", str(tmp_path)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no stale" in out
        assert (tmp_path / "cvcpkg-new-xyz").exists()

    def test_clean_all_ignores_age(self, tmp_path, capsys):
        self._populate(tmp_path, ["cvcpkg-fresh-abc"], age_minutes=0)
        ret = main(["clean", "--work-dir", str(tmp_path), "--all"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "removed" in out
        assert not (tmp_path / "cvcpkg-fresh-abc").exists()

    def test_clean_dry_run(self, tmp_path, capsys):
        self._populate(tmp_path, ["cvcpkg-test-111"], age_minutes=180)
        ret = main(["clean", "--work-dir", str(tmp_path), "--dry-run"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "would remove" in out
        # Dir should still exist
        assert (tmp_path / "cvcpkg-test-111").exists()

    def test_clean_older_than(self, tmp_path, capsys):
        self._populate(tmp_path, ["cvcpkg-old-aaa"], age_minutes=60)
        self._populate(tmp_path, ["cvcpkg-older-bbb"], age_minutes=200)
        ret = main(["clean", "--work-dir", str(tmp_path), "--older-than", "90"])
        assert ret == 0
        # Only the 200-minute old one should be removed
        assert (tmp_path / "cvcpkg-old-aaa").exists()
        assert not (tmp_path / "cvcpkg-older-bbb").exists()


# ── publish by recipe name ──────────────────────────────────────


class TestPublishRecipeName:
    """Tests for recipe-name resolution in the publish command."""

    def _make_archive(self, d, name, version, platform, arch, config, link):
        """Create a fake archive file with valid naming."""
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{name}-{version}-{platform}-{arch}-{config}-{link}.tar.gz"
        p = d / fname
        p.write_bytes(b"fake")
        return p

    def test_publish_help_shows_recipe_name_examples(self, capsys):
        ret = main(["publish", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--output-dir" in out
        assert "--all" in out
        assert "recipe name" in out.lower() or "recipe names" in out.lower()

    def test_resolve_by_recipe_name(self, tmp_path):
        from cvcpkg.cli import _resolve_publish_archives

        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared")
        result = _resolve_publish_archives(
            ("zlib",), str(tmp_path), "linux", "x86_64", "release", "shared"
        )
        assert len(result) == 1
        assert "zlib-" in result[0].name

    def test_resolve_file_path_deprecated(self, tmp_path):
        import warnings

        from cvcpkg.cli import _resolve_publish_archives

        archive = self._make_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_publish_archives(
                (str(archive),), str(tmp_path), "linux", "x86_64", "release", "shared"
            )
            assert len(result) == 1
            assert result[0] == archive.resolve()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()

    def test_resolve_recipe_not_found(self, tmp_path):
        from cvcpkg.cli import _resolve_publish_archives

        tmp_path.mkdir(exist_ok=True)
        with pytest.raises(click.ClickException, match="no archive found"):
            _resolve_publish_archives(
                ("nonexistent",), str(tmp_path), "linux", "x86_64", "release", "shared"
            )

    def test_resolve_all_archives(self, tmp_path):
        from cvcpkg.cli import _resolve_all_archives

        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared")
        self._make_archive(tmp_path, "grpc", "1.60.0+cvc.1", "linux", "x86_64", "release", "shared")
        # Also create a sig file and a different platform archive — both should be skipped
        (tmp_path / "zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz.sig").write_bytes(b"sig")
        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "macos", "arm64", "release", "shared")

        result = _resolve_all_archives(str(tmp_path), "linux", "x86_64", "release", "shared")
        assert len(result) == 2
        names = {r.name for r in result}
        assert "zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz" in names
        assert "grpc-1.60.0+cvc.1-linux-x86_64-release-shared.tar.gz" in names

    def test_resolve_all_empty_dir(self, tmp_path):
        from cvcpkg.cli import _resolve_all_archives

        result = _resolve_all_archives(str(tmp_path), "linux", "x86_64", "release", "shared")
        assert result == []

    def test_publish_no_args_no_all_errors(self, capsys):
        ret = main(["publish", "--server", "https://fake.example.com", "--token", "tok"])
        assert ret != 0

    def test_resolve_multiple_versions_picks_latest(self, tmp_path):
        """When multiple archives exist for a recipe, the latest (last sorted) is used."""
        from cvcpkg.cli import _resolve_publish_archives

        self._make_archive(tmp_path, "zlib", "1.2.0+cvc.1", "linux", "x86_64", "release", "shared")
        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared")
        result = _resolve_publish_archives(
            ("zlib",), str(tmp_path), "linux", "x86_64", "release", "shared"
        )
        assert len(result) == 1
        assert "1.3.1" in result[0].name

    def test_resolve_output_dir_not_exists(self, tmp_path):
        """Error when output dir does not exist."""
        from cvcpkg.cli import _resolve_publish_archives

        with pytest.raises(click.ClickException, match="output directory does not exist"):
            _resolve_publish_archives(
                ("zlib",), str(tmp_path / "no-such-dir"), "linux", "x86_64", "release", "shared"
            )

    def test_resolve_mixed_names_and_paths(self, tmp_path):
        """Mix of recipe names and file paths both resolve correctly."""
        import warnings

        from cvcpkg.cli import _resolve_publish_archives

        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared")
        a2 = self._make_archive(
            tmp_path, "grpc", "1.60.0+cvc.1", "linux", "x86_64", "release", "shared"
        )
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _resolve_publish_archives(
                ("zlib", str(a2)), str(tmp_path), "linux", "x86_64", "release", "shared"
            )
        assert len(result) == 2

    def test_resolve_all_filters_config_and_link(self, tmp_path):
        """_resolve_all_archives only returns archives matching config/link."""
        from cvcpkg.cli import _resolve_all_archives

        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared")
        self._make_archive(tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "debug", "shared")
        self._make_archive(tmp_path, "grpc", "1.60.0+cvc.1", "linux", "x86_64", "release", "static")
        result = _resolve_all_archives(str(tmp_path), "linux", "x86_64", "release", "shared")
        assert len(result) == 1
        assert "zlib-" in result[0].name

    def test_resolve_all_nonexistent_dir(self, tmp_path):
        """_resolve_all_archives returns [] for a missing directory."""
        from cvcpkg.cli import _resolve_all_archives

        result = _resolve_all_archives(
            str(tmp_path / "no-such-dir"), "linux", "x86_64", "release", "shared"
        )
        assert result == []


# ── manifest extraction ─────────────────────────────────────────


class TestExtractManifest:
    """Tests for _extract_manifest from tar.gz and zip archives."""

    def _make_tar_archive(self, d, name, manifest_data):
        """Create a .tar.gz archive with a manifest.yaml inside."""
        import io
        import tarfile

        d.mkdir(parents=True, exist_ok=True)
        archive = d / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            content = yaml.dump(manifest_data).encode()
            info = tarfile.TarInfo(name="manifest.yaml")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        return archive

    def _make_zip_archive(self, d, name, manifest_data):
        """Create a .zip archive with a manifest.yaml inside."""
        import zipfile

        d.mkdir(parents=True, exist_ok=True)
        archive = d / f"{name}.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.yaml", yaml.dump(manifest_data))
        return archive

    def test_extract_from_tar(self, tmp_path):
        from cvcpkg.cli import _extract_manifest

        manifest_data = {
            "bundle": {"name": "zlib", "version": "1.3.1+cvc.1", "platform": "linux"},
            "meta": {"description": "zlib compression"},
        }
        archive = self._make_tar_archive(tmp_path, "zlib", manifest_data)
        result = _extract_manifest(archive)
        assert result["bundle"]["name"] == "zlib"
        assert result["bundle"]["version"] == "1.3.1+cvc.1"
        assert result["meta"]["description"] == "zlib compression"

    def test_extract_from_zip(self, tmp_path):
        from cvcpkg.cli import _extract_manifest

        manifest_data = {
            "bundle": {"name": "boost", "version": "1.85.0+cvc.1", "platform": "windows"},
        }
        archive = self._make_zip_archive(tmp_path, "boost", manifest_data)
        result = _extract_manifest(archive)
        assert result["bundle"]["name"] == "boost"

    def test_extract_missing_manifest(self, tmp_path):
        """Archive without manifest.yaml raises ClickException."""
        import io
        import tarfile

        archive = tmp_path / "empty.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            content = b"not a manifest"
            info = tarfile.TarInfo(name="README.md")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        from cvcpkg.cli import _extract_manifest

        with pytest.raises(click.ClickException, match="no manifest.yaml"):
            _extract_manifest(archive)

    def test_extract_corrupted_archive(self, tmp_path):
        """Corrupted archive raises ClickException."""
        from cvcpkg.cli import _extract_manifest

        archive = tmp_path / "bad.tar.gz"
        archive.write_bytes(b"this is not a valid archive")
        with pytest.raises(click.ClickException, match="cannot read archive"):
            _extract_manifest(archive)

    def test_extract_nested_manifest(self, tmp_path):
        """manifest.yaml inside a subdirectory in the archive."""
        import io
        import tarfile

        from cvcpkg.cli import _extract_manifest

        manifest_data = {"bundle": {"name": "nested", "version": "1.0"}}
        archive = tmp_path / "nested.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            content = yaml.dump(manifest_data).encode()
            info = tarfile.TarInfo(name="nested-1.0/manifest.yaml")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        result = _extract_manifest(archive)
        assert result["bundle"]["name"] == "nested"


# ── _publish_to_server unit tests ───────────────────────────────


class TestPublishToServer:
    """Tests for _publish_to_server with mocked HTTP."""

    def _make_manifest_archive(self, d, name, version, platform, arch, config, link):
        """Create an archive with a real embedded manifest.yaml."""
        import io
        import tarfile

        d.mkdir(parents=True, exist_ok=True)
        fname = f"{name}-{version}-{platform}-{arch}-{config}-{link}.tar.gz"
        archive = d / fname
        manifest_data = {
            "bundle": {
                "name": name,
                "version": version,
                "platform": platform,
                "arch": arch,
                "config": config,
                "link": link,
            },
            "meta": {
                "recipe_sha256": "abc123",
                "description": f"{name} library",
            },
        }
        with tarfile.open(archive, "w:gz") as tf:
            content = yaml.dump(manifest_data).encode()
            info = tarfile.TarInfo(name="manifest.yaml")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        return archive

    def test_publish_simple_success(self, tmp_path):
        """_publish_to_server uploads small archive via _publish_simple."""
        from cvcpkg.cli import _publish_to_server

        archive = self._make_manifest_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )

        with (
            mock.patch("cvcpkg.cli._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish_simple", return_value="published") as mock_simple,
        ):
            _publish_to_server(
                "https://pkg.example.com",
                "cvctok_test",
                [archive],
                release_tag="",
                chunked_threshold=10 * 1024 * 1024,
                org="",
            )
            mock_simple.assert_called_once()
            args = mock_simple.call_args
            assert args[0][0] == "https://pkg.example.com"  # base
            assert args[0][2]["name"] == "zlib"  # params

    def test_publish_skips_existing_variant(self, tmp_path, capsys):
        """_publish_to_server skips when _variant_exists returns True."""
        from cvcpkg.cli import _publish_to_server

        archive = self._make_manifest_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )

        with (
            mock.patch("cvcpkg.cli._variant_exists", return_value=True),
            mock.patch("cvcpkg.cli._publish_simple") as mock_simple,
        ):
            _publish_to_server(
                "https://pkg.example.com",
                "cvctok_test",
                [archive],
                release_tag="",
                chunked_threshold=10 * 1024 * 1024,
                org="",
            )
            mock_simple.assert_not_called()
        out = capsys.readouterr().out
        assert "skipping" in out.lower()

    def test_publish_missing_name_raises(self, tmp_path):
        """_publish_to_server errors when manifest has no name."""
        import io
        import tarfile

        from cvcpkg.cli import _publish_to_server

        archive = tmp_path / "bad.tar.gz"
        manifest_data = {"bundle": {"name": "", "version": ""}, "meta": {}}
        with tarfile.open(archive, "w:gz") as tf:
            content = yaml.dump(manifest_data).encode()
            info = tarfile.TarInfo(name="manifest.yaml")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        with mock.patch("cvcpkg.cli._variant_exists", return_value=False):
            with pytest.raises(click.ClickException, match="manifest missing name"):
                _publish_to_server(
                    "https://pkg.example.com",
                    "cvctok_test",
                    [archive],
                    release_tag="",
                    chunked_threshold=10 * 1024 * 1024,
                    org="",
                )

    def test_publish_with_org(self, tmp_path, capsys):
        """_publish_to_server passes org to params and display."""
        from cvcpkg.cli import _publish_to_server

        archive = self._make_manifest_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )

        with (
            mock.patch("cvcpkg.cli._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish_simple", return_value="published") as mock_simple,
        ):
            _publish_to_server(
                "https://pkg.example.com",
                "cvctok_test",
                [archive],
                release_tag="v1.0",
                chunked_threshold=10 * 1024 * 1024,
                org="myorg",
            )
            params = mock_simple.call_args[0][2]
            assert params["org"] == "myorg"
            assert params["release_tag"] == "v1.0"
        out = capsys.readouterr().out
        assert "myorg/zlib" in out

    def test_publish_uses_chunked_for_large_files(self, tmp_path):
        """_publish_to_server calls _publish_chunked when file > threshold."""
        from cvcpkg.cli import _publish_to_server

        archive = self._make_manifest_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )

        with (
            mock.patch("cvcpkg.cli._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish_chunked", return_value="published") as mock_chunked,
        ):
            _publish_to_server(
                "https://pkg.example.com",
                "cvctok_test",
                [archive],
                release_tag="",
                chunked_threshold=1,  # tiny threshold to force chunked
                org="",
            )
            mock_chunked.assert_called_once()

    def test_publish_failure_continues_and_reports(self, tmp_path, capsys):
        """_publish_to_server collects failures and raises at the end."""
        from cvcpkg.cli import _publish_to_server

        a1 = self._make_manifest_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )
        a2 = self._make_manifest_archive(
            tmp_path, "grpc", "1.60.0+cvc.1", "linux", "x86_64", "release", "shared"
        )

        with (
            mock.patch("cvcpkg.cli._variant_exists", return_value=False),
            mock.patch(
                "cvcpkg.cli._publish_simple",
                side_effect=click.ClickException("upload failed"),
            ),
        ):
            with pytest.raises(click.ClickException, match="error"):
                _publish_to_server(
                    "https://pkg.example.com",
                    "cvctok_test",
                    [a1, a2],
                    release_tag="",
                    chunked_threshold=10 * 1024 * 1024,
                    org="",
                )
        err = capsys.readouterr().err
        assert "ERROR" in err

    def test_publish_multiple_archives_success(self, tmp_path, capsys):
        """_publish_to_server reports correct count for multiple archives."""
        from cvcpkg.cli import _publish_to_server

        a1 = self._make_manifest_archive(
            tmp_path, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )
        a2 = self._make_manifest_archive(
            tmp_path, "grpc", "1.60.0+cvc.1", "linux", "x86_64", "release", "shared"
        )

        with (
            mock.patch("cvcpkg.cli._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish_simple", return_value="published"),
        ):
            _publish_to_server(
                "https://pkg.example.com",
                "cvctok_test",
                [a1, a2],
                release_tag="",
                chunked_threshold=10 * 1024 * 1024,
                org="",
            )
        out = capsys.readouterr().out
        assert "2/2" in out


# ── _publish_to_backend unit tests ──────────────────────────────


class TestPublishToBackend:
    """Tests for _publish_to_backend with mocked storage."""

    def test_backend_uploads_all_archives(self, tmp_path):
        """_publish_to_backend calls backend.put for each archive."""
        from cvcpkg.cli import _publish_to_backend

        a1 = tmp_path / "zlib.tar.gz"
        a1.write_bytes(b"archive1")
        a2 = tmp_path / "grpc.tar.gz"
        a2.write_bytes(b"archive2")

        mock_backend = mock.MagicMock()
        with mock.patch("cvcpkg.storage.get_backend", return_value=mock_backend) as mock_get:
            _publish_to_backend("s3://bucket/path", [a1, a2])
            mock_get.assert_called_once_with("s3://bucket/path")
            assert mock_backend.put.call_count == 2
            # Check URIs
            calls = mock_backend.put.call_args_list
            assert "zlib.tar.gz" in calls[0][0][0]
            assert "grpc.tar.gz" in calls[1][0][0]

    def test_backend_missing_file_raises(self, tmp_path):
        """_publish_to_backend raises ClickException for missing file."""
        from cvcpkg.cli import _publish_to_backend

        missing = tmp_path / "nonexistent.tar.gz"
        mock_backend = mock.MagicMock()
        with mock.patch("cvcpkg.storage.get_backend", return_value=mock_backend):
            with pytest.raises(click.ClickException, match="file not found"):
                _publish_to_backend("s3://bucket/path", [missing])

    def test_backend_not_implemented_raises(self, tmp_path):
        """_publish_to_backend raises when backend doesn't support put."""
        from cvcpkg.cli import _publish_to_backend

        archive = tmp_path / "test.tar.gz"
        archive.write_bytes(b"data")
        mock_backend = mock.MagicMock()
        mock_backend.put.side_effect = NotImplementedError("read-only")
        with mock.patch("cvcpkg.storage.get_backend", return_value=mock_backend):
            with pytest.raises(click.ClickException, match="does not support uploads"):
                _publish_to_backend("s3://bucket/path", [archive])


# ── _variant_exists unit tests ──────────────────────────────────


class TestVariantExists:
    """Tests for _variant_exists with mocked httpx."""

    def test_variant_found(self):
        from cvcpkg.cli import _variant_exists

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "packages": [
                {
                    "version": "1.3.1+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                }
            ]
        }
        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with mock.patch("httpx.Client", return_value=mock_client):
            assert (
                _variant_exists(
                    "https://pkg.example.com",
                    {"Authorization": "Bearer tok"},
                    "zlib",
                    "1.3.1+cvc.1",
                    "linux",
                    "x86_64",
                    "release",
                    "shared",
                )
                is True
            )

    def test_variant_not_found(self):
        from cvcpkg.cli import _variant_exists

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"packages": []}
        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with mock.patch("httpx.Client", return_value=mock_client):
            assert (
                _variant_exists(
                    "https://pkg.example.com",
                    {"Authorization": "Bearer tok"},
                    "zlib",
                    "1.3.1+cvc.1",
                    "linux",
                    "x86_64",
                    "release",
                    "shared",
                )
                is False
            )

    def test_variant_server_error_returns_false(self):
        from cvcpkg.cli import _variant_exists

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 500
        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with mock.patch("httpx.Client", return_value=mock_client):
            assert (
                _variant_exists(
                    "https://pkg.example.com",
                    {},
                    "zlib",
                    "1.3.1+cvc.1",
                    "linux",
                    "x86_64",
                    "release",
                    "shared",
                )
                is False
            )

    def test_variant_network_error_returns_false(self):
        from cvcpkg.cli import _variant_exists

        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("connection refused")

        with mock.patch("httpx.Client", return_value=mock_client):
            assert (
                _variant_exists(
                    "https://pkg.example.com",
                    {},
                    "zlib",
                    "1.3.1+cvc.1",
                    "linux",
                    "x86_64",
                    "release",
                    "shared",
                )
                is False
            )

    def test_variant_wrong_platform_not_matched(self):
        """Variant exists for different platform but not for requested one."""
        from cvcpkg.cli import _variant_exists

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "packages": [
                {
                    "version": "1.3.1+cvc.1",
                    "platform": "macos",
                    "arch": "arm64",
                    "build_type": "release",
                    "link": "shared",
                }
            ]
        }
        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with mock.patch("httpx.Client", return_value=mock_client):
            assert (
                _variant_exists(
                    "https://pkg.example.com",
                    {},
                    "zlib",
                    "1.3.1+cvc.1",
                    "linux",
                    "x86_64",
                    "release",
                    "shared",
                )
                is False
            )


# ── _publish_simple unit tests ──────────────────────────────────


class TestPublishSimple:
    """Tests for _publish_simple with mocked httpx."""

    def _mock_client(self, resp):
        """Create a mock httpx.Client context manager returning resp."""
        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(return_value=client)
        client.__exit__ = mock.MagicMock(return_value=False)
        client.post.return_value = resp
        return client

    def test_simple_publish_200(self, tmp_path):
        from cvcpkg.cli import _publish_simple

        archive = tmp_path / "test.tar.gz"
        archive.write_bytes(b"data")
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"sha256": "abc123"}
        with mock.patch("httpx.Client", return_value=self._mock_client(resp)):
            result = _publish_simple("https://pkg.example.com", {}, {}, archive)
        assert result == "published"

    def test_simple_publish_409_skipped(self, tmp_path):
        from cvcpkg.cli import _publish_simple

        archive = tmp_path / "test.tar.gz"
        archive.write_bytes(b"data")
        resp = mock.MagicMock()
        resp.status_code = 409
        resp.json.return_value = {"detail": "already exists"}
        with mock.patch("httpx.Client", return_value=self._mock_client(resp)):
            result = _publish_simple("https://pkg.example.com", {}, {}, archive)
        assert result == "skipped"

    def test_simple_publish_error_raises(self, tmp_path):
        from cvcpkg.cli import _publish_simple

        archive = tmp_path / "test.tar.gz"
        archive.write_bytes(b"data")
        resp = mock.MagicMock()
        resp.status_code = 500
        resp.text = "internal server error"
        with mock.patch("httpx.Client", return_value=self._mock_client(resp)):
            with pytest.raises(click.ClickException, match="publish failed"):
                _publish_simple("https://pkg.example.com", {}, {}, archive)


# ── publish CLI integration tests ───────────────────────────────


class TestPublishCLIIntegration:
    """Integration tests for the publish command via CLI with mocked backends."""

    def _make_manifest_archive(self, d, name, version, platform, arch, config, link):
        """Create an archive with a real embedded manifest.yaml."""
        import io
        import tarfile

        d.mkdir(parents=True, exist_ok=True)
        fname = f"{name}-{version}-{platform}-{arch}-{config}-{link}.tar.gz"
        archive = d / fname
        manifest_data = {
            "bundle": {
                "name": name,
                "version": version,
                "platform": platform,
                "arch": arch,
                "config": config,
                "link": link,
            },
            "meta": {"recipe_sha256": "abc"},
        }
        with tarfile.open(archive, "w:gz") as tf:
            content = yaml.dump(manifest_data).encode()
            info = tarfile.TarInfo(name="manifest.yaml")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        return archive

    def test_publish_dest_with_recipe_name(self, tmp_path, capsys):
        """Full CLI: publish recipe-name --dest file:// works end-to-end."""
        dist = tmp_path / "dist"
        dest = tmp_path / "dest"
        dest.mkdir()
        self._make_manifest_archive(
            dist, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )
        with mock.patch("cvcpkg.platform.detect_arch", return_value="x86_64"):
            ret = main(
                [
                    "publish",
                    "zlib",
                    "--dest",
                    f"file://{dest}",
                    "--output-dir",
                    str(dist),
                    "--platform",
                    "linux",
                ]
            )
        assert ret == 0
        out = capsys.readouterr().out
        assert "published" in out.lower()
        assert any(f.name.startswith("zlib-") for f in dest.iterdir())

    def test_publish_all_dest(self, tmp_path, capsys):
        """Full CLI: publish --all --dest file:// uploads all matching archives."""
        dist = tmp_path / "dist"
        dest = tmp_path / "dest"
        dest.mkdir()
        self._make_manifest_archive(
            dist, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )
        self._make_manifest_archive(
            dist, "grpc", "1.60.0+cvc.1", "linux", "x86_64", "release", "shared"
        )
        with mock.patch("cvcpkg.platform.detect_arch", return_value="x86_64"):
            ret = main(
                [
                    "publish",
                    "--all",
                    "--dest",
                    f"file://{dest}",
                    "--output-dir",
                    str(dist),
                    "--platform",
                    "linux",
                ]
            )
        assert ret == 0
        out = capsys.readouterr().out
        assert "2 archive(s)" in out

    def test_publish_server_with_recipe_name(self, tmp_path, capsys):
        """Full CLI: publish recipe-name --server mocked end-to-end."""
        dist = tmp_path / "dist"
        self._make_manifest_archive(
            dist, "zlib", "1.3.1+cvc.1", "linux", "x86_64", "release", "shared"
        )
        with (
            mock.patch("cvcpkg.platform.detect_arch", return_value="x86_64"),
            mock.patch("cvcpkg.cli._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish_simple", return_value="published"),
        ):
            ret = main(
                [
                    "publish",
                    "zlib",
                    "--server",
                    "https://pkg.example.com",
                    "--token",
                    "cvctok_test",
                    "--output-dir",
                    str(dist),
                    "--platform",
                    "linux",
                ]
            )
        assert ret == 0
        out = capsys.readouterr().out
        assert "1/1" in out

    def test_publish_help_shows_dest(self, capsys):
        """Help text includes --dest option."""
        ret = main(["publish", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--dest" in out
        assert "--server" in out
        assert "storage backend" in out.lower() or "Storage" in out


# ── --local flag ────────────────────────────────────────────────


class TestLocalFlag:
    """Verify --local / CVCPKG_LOCAL flag is present on the right commands."""

    @pytest.mark.parametrize("subcmd", ["build", "pack", "build-all", "pack-all", "install"])
    def test_local_flag_in_help(self, subcmd, capsys):
        ret = main([subcmd, "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--local" in out
        assert "CVCPKG_LOCAL" in out

    def test_local_flag_not_on_recipes(self, capsys):
        """The 'recipes' command should NOT have --local."""
        ret = main(["recipes", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--local" not in out

    def test_install_local_implies_fallback(self, tmp_path, capsys, monkeypatch):
        """--local on install should imply --fallback-to-source."""
        # Verify _try_pull_server_recipes is NOT called when --local is set
        call_tracker = {"called": False}

        def mock_pull():
            call_tracker["called"] = True
            return ()

        monkeypatch.setattr("cvcpkg.cli._try_pull_server_recipes", mock_pull)

        recipes_dir = tmp_path / "empty_recipes"
        recipes_dir.mkdir()
        prefix = tmp_path / "prefix"
        ret = main([
            "install",
            "nonexistent-pkg-xyz",
            "--prefix", str(prefix),
            "--local",
            "--recipes-dir", str(recipes_dir),
        ])
        # With --local, _try_pull_server_recipes should NOT be called
        assert not call_tracker["called"]

    def test_local_env_var(self, tmp_path, capsys, monkeypatch):
        """CVCPKG_LOCAL=1 should have the same effect as --local."""
        monkeypatch.setenv("CVCPKG_LOCAL", "1")
        # Just check help shows it — the env var is documented
        ret = main(["build", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "CVCPKG_LOCAL" in out

    def test_build_local_no_server_contact(self, tmp_path, capsys, monkeypatch):
        """build --local should not call _try_pull_server_recipes."""
        # Mock _try_pull_server_recipes to track if it's called
        call_tracker = {"called": False}

        def mock_pull():
            call_tracker["called"] = True
            return ()

        monkeypatch.setattr("cvcpkg.cli._try_pull_server_recipes", mock_pull)

        # Use real recipes dir so recipe.yaml loads properly
        from cvcpkg.builder import find_recipes_dir
        try:
            real_recipes = str(find_recipes_dir())
        except Exception:
            pytest.skip("no recipes directory found")

        ret = main([
            "build", "zlib",
            "--local",
            "--no-deps",
            "--recipes-dir", real_recipes,
            "--prefix", str(tmp_path / "prefix"),
        ])
        # With --local, _try_pull_server_recipes should NOT be called
        assert not call_tracker["called"]


# ── _try_pull_server_recipes ────────────────────────────────────


class TestTryPullServerRecipes:
    """Unit tests for the _try_pull_server_recipes helper."""

    def test_returns_empty_on_connection_error(self, monkeypatch, capsys):
        """When the server is unreachable, returns empty tuple."""
        monkeypatch.setenv("CVCPKG_SERVER_URL", "https://localhost:1")
        from cvcpkg.cli import _try_pull_server_recipes

        result = _try_pull_server_recipes()
        assert result == ()
        err = capsys.readouterr().err
        assert "falling back to local recipes" in err

    def test_returns_empty_on_http_error(self, monkeypatch, capsys):
        """When server returns non-200, returns empty tuple."""
        import httpx

        monkeypatch.setenv("CVCPKG_SERVER_URL", "https://example.com")

        class FakeResponse:
            status_code = 500
            content = b""

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        from cvcpkg.cli import _try_pull_server_recipes

        result = _try_pull_server_recipes()
        assert result == ()
        err = capsys.readouterr().err
        assert "HTTP 500" in err

    def test_returns_dir_on_success(self, monkeypatch, capsys, tmp_path):
        """When server returns a valid bundle, extracts and returns dir."""
        import io
        import tarfile

        monkeypatch.setenv("CVCPKG_SERVER_URL", "https://example.com")

        # Create a valid tar.gz in memory
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"name: zlib\n"
            info = tarfile.TarInfo(name="zlib/recipe.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        bundle_bytes = buf.getvalue()

        class FakeResponse:
            status_code = 200
            content = bundle_bytes

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        from cvcpkg.cli import _try_pull_server_recipes

        result = _try_pull_server_recipes()
        assert len(result) == 1
        assert Path(result[0]).is_dir()
        out = capsys.readouterr().out
        assert "using recipes from" in out

    def test_passes_token_header(self, monkeypatch, capsys):
        """When CVCPKG_TOKEN is set, it's included in the request."""
        monkeypatch.setenv("CVCPKG_SERVER_URL", "https://example.com")
        monkeypatch.setenv("CVCPKG_TOKEN", "cvctok_secret")

        captured_headers = {}

        class FakeResponse:
            status_code = 500
            content = b""

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def get(self, url, headers=None, **kw):
                captured_headers.update(headers or {})
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        from cvcpkg.cli import _try_pull_server_recipes

        _try_pull_server_recipes()
        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"] == "Bearer cvctok_secret"


# ── recipe subcommands help ─────────────────────────────────────


class TestRecipeSubcommands:
    """Test help text and basic validation for recipe sub-commands."""

    def test_recipe_publish_help(self, capsys):
        ret = main(["recipe", "publish", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--token" in out
        assert "placeholder" in out.lower()

    def test_recipe_pull_help(self, capsys):
        ret = main(["recipe", "pull", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--output-dir" in out

    def test_recipe_pull_all_help(self, capsys):
        ret = main(["recipe", "pull-all", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--output-dir" in out
        assert "bundle" in out.lower() or "full recipe set" in out.lower()

    def test_recipe_push_all_help(self, capsys):
        ret = main(["recipe", "push-all", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--token" in out
        assert "--recipes-dir" in out

    @pytest.mark.parametrize("subcmd", ["publish", "pull", "pull-all", "push-all"])
    def test_recipe_subcommand_listed(self, subcmd, capsys):
        """All new recipe subcommands appear in 'recipe --help'."""
        ret = main(["recipe", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert subcmd in out


# ── recipe publish functional test ─────────────────────────────


class TestRecipePublishFunctional:
    """Test recipe publish command with mocked HTTP."""

    def test_publish_pushes_and_registers(self, tmp_path, capsys, monkeypatch):
        """recipe publish should push a bundle then register a placeholder."""
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        zlib = recipes_dir / "zlib"
        zlib.mkdir()
        (zlib / "recipe.yaml").write_text(
            yaml.dump({
                "recipe": {
                    "name": "zlib",
                    "upstream_version": "1.3.1",
                    "description": "compression",
                    "homepage": "https://zlib.net",
                    "license": "zlib",
                    "maintainer": "test-user",
                    "platforms": ["linux"],
                    "deps": [],
                },
                "cvc_revision": 2,
            })
        )
        (zlib / "linux.sh").write_text("#!/bin/sh\necho ok\n")

        requests_made = []

        class FakeResponse:
            def __init__(self, status_code=200):
                self.status_code = status_code
                self.text = '{"ok": true}'
                self.content = b'{"ok": true}'
            def json(self):
                return {"ok": True, "status": "registered"}

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def post(self, url, **kw):
                requests_made.append(("POST", url, kw))
                return FakeResponse()
            def get(self, url, **kw):
                requests_made.append(("GET", url, kw))
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        ret = main([
            "recipe", "publish", "zlib",
            "--server", "https://test.example.com",
            "--token", "cvctok_test",
            "--recipes-dir", str(recipes_dir),
        ])
        assert ret == 0

        out = capsys.readouterr().out
        assert "pushed" in out.lower()
        assert "registered" in out.lower()

        # Should have made 2 POST requests: push + register
        posts = [r for r in requests_made if r[0] == "POST"]
        assert len(posts) == 2
        assert "/v1/recipes/zlib" in posts[0][1]
        assert "/v1/recipes/zlib/register" in posts[1][1]

    def test_publish_recipe_not_found(self, tmp_path, capsys, monkeypatch):
        """recipe publish with nonexistent recipe should fail."""
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()

        ret = main([
            "recipe", "publish", "nonexistent",
            "--server", "https://test.example.com",
            "--token", "cvctok_test",
            "--recipes-dir", str(recipes_dir),
        ])
        assert ret != 0
        combined = capsys.readouterr()
        assert "not found" in (combined.out + combined.err).lower()


# ── recipe pull functional test ────────────────────────────────


class TestRecipePullFunctional:
    """Test recipe pull command with mocked HTTP."""

    def test_pull_downloads_and_extracts(self, tmp_path, capsys, monkeypatch):
        """recipe pull should download and extract a recipe bundle."""
        import io
        import tarfile

        output_dir = tmp_path / "recipes"

        # Create a valid tar.gz bundle
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"name: zlib\n"
            info = tarfile.TarInfo(name="zlib/recipe.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        bundle_bytes = buf.getvalue()

        class FakeResponse:
            status_code = 200
            content = bundle_bytes
            text = ""
            def json(self):
                return {}

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        ret = main([
            "recipe", "pull", "zlib",
            "--server", "https://test.example.com",
            "--output-dir", str(output_dir),
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "extracted" in out.lower()
        assert (output_dir / "zlib" / "recipe.yaml").is_file()

    def test_pull_server_error(self, tmp_path, capsys, monkeypatch):
        """recipe pull should fail gracefully on server error."""
        class FakeResponse:
            status_code = 404
            text = "not found"
            def json(self):
                return {"detail": "not found"}

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        ret = main([
            "recipe", "pull", "nonexistent",
            "--server", "https://test.example.com",
            "--output-dir", str(tmp_path / "out"),
        ])
        assert ret != 0


# ── recipe pull-all functional test ────────────────────────────


class TestRecipePullAllFunctional:
    """Test recipe pull-all command with mocked HTTP."""

    def test_pull_all_downloads_bundle(self, tmp_path, capsys, monkeypatch):
        """recipe pull-all should download and extract the full bundle."""
        import io
        import tarfile

        output_dir = tmp_path / "recipes"

        # Create a bundle with two recipes
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ["zlib", "boost"]:
                data = f"name: {name}\n".encode()
                info = tarfile.TarInfo(name=f"{name}/recipe.yaml")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        bundle_bytes = buf.getvalue()

        class FakeResponse:
            status_code = 200
            content = bundle_bytes
            text = ""
            def json(self):
                return {}

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        ret = main([
            "recipe", "pull-all",
            "--server", "https://test.example.com",
            "--output-dir", str(output_dir),
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "2 recipes extracted" in out
        assert (output_dir / "zlib" / "recipe.yaml").is_file()
        assert (output_dir / "boost" / "recipe.yaml").is_file()


# ── recipe push-all functional test ────────────────────────────


class TestRecipePushAllFunctional:
    """Test recipe push-all command with mocked HTTP."""

    def test_push_all_pushes_each_recipe(self, tmp_path, capsys, monkeypatch):
        """recipe push-all should push each recipe in the directory."""
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()

        for name in ["boost", "zlib"]:
            d = recipes_dir / name
            d.mkdir()
            (d / "recipe.yaml").write_text(
                yaml.dump({
                    "recipe": {
                        "name": name,
                        "upstream_version": "1.0",
                        "platforms": ["linux"],
                        "deps": [],
                    },
                    "cvc_revision": 1,
                })
            )

        # Skip _common and dot-dirs
        (recipes_dir / "_common").mkdir()
        (recipes_dir / ".hidden").mkdir()

        pushed_names = []

        class FakeResponse:
            status_code = 200
            text = '{"ok": true}'
            def json(self):
                return {"ok": True}

        class FakeClient:
            def __init__(self, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def post(self, url, **kw):
                # Extract recipe name from URL
                parts = url.rstrip("/").split("/")
                name_idx = parts.index("recipes") + 1
                if name_idx < len(parts):
                    pushed_names.append(parts[name_idx])
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        ret = main([
            "recipe", "push-all",
            "--server", "https://test.example.com",
            "--token", "cvctok_test",
            "--recipes-dir", str(recipes_dir),
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "boost" in out
        assert "zlib" in out
        # Should have pushed exactly 2 recipes (not _common or .hidden)
        assert sorted(pushed_names) == ["boost", "zlib"]


# ── recipe list / delete CLI tests ─────────────────────────────


class TestRecipeListCLI:
    """Test recipe list command with mocked HTTP."""

    def test_recipe_list_shows_recipes(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "total": 2,
                    "recipes": [
                        {"name": "zlib", "version": "1.3.1", "bundle_size": 4096, "updated_at": "2026-01-01"},
                        {"name": "boost", "version": "1.85", "bundle_size": 12000, "updated_at": "2026-01-02"},
                    ],
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["recipe", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out
        assert "boost" in out

    def test_recipe_list_empty(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"total": 0, "recipes": []}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["recipe", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no recipes" in out.lower()

    def test_recipe_list_server_error(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 500
            text = "server error"
            def json(self):
                return {"detail": "server error"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["recipe", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret != 0


class TestRecipeDeleteCLI:
    """Test recipe delete command with mocked HTTP."""

    def test_recipe_delete_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = '{"ok": true}'
            def json(self):
                return {"ok": True}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "recipe", "delete", "zlib",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "deleted" in out.lower()

    def test_recipe_delete_not_found(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 404
            text = "not found"
            def json(self):
                return {"detail": "not found"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "recipe", "delete", "nonexistent",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret != 0


# ── webhook CLI tests ──────────────────────────────────────────


class TestWebhookCLIHelp:
    """Verify webhook subcommands appear in help and accept --help."""

    @pytest.mark.parametrize("subcmd", ["register", "list", "info", "update", "delete", "test"])
    def test_webhook_subcommand_help(self, subcmd, capsys):
        ret = main(["webhook", subcmd, "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--token" in out

    def test_webhook_group_lists_subcommands(self, capsys):
        ret = main(["webhook", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        for cmd in ("register", "list", "info", "update", "delete", "test"):
            assert cmd in out


class TestWebhookRegisterCLI:
    """Test webhook register command with mocked HTTP."""

    def test_register_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"id": 42, "url": "https://hook.example.com/cb", "events": ["build.completed"]}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "register", "https://hook.example.com/cb",
            "-e", "build.completed",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "42" in out
        assert "registered" in out.lower()

    def test_register_server_error(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 400
            text = "bad request"
            def json(self):
                return {"detail": "bad request"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "register", "https://hook.example.com/cb",
            "-e", "build.completed",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret != 0


class TestWebhookListCLI:
    """Test webhook list command with mocked HTTP."""

    def test_list_shows_webhooks(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "total": 1,
                    "webhooks": [
                        {"id": 1, "url": "https://h.example.com", "events": ["build.completed"], "active": True},
                    ],
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["webhook", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "h.example.com" in out
        assert "Total: 1" in out

    def test_list_empty(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"total": 0, "webhooks": []}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["webhook", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Total: 0" in out


class TestWebhookInfoCLI:
    """Test webhook info command with mocked HTTP."""

    def test_info_shows_details(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"id": 5, "url": "https://h.example.com", "events": ["e1"], "active": True}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["webhook", "info", "5", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "id: 5" in out
        assert "h.example.com" in out

    def test_info_not_found(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 404
            text = "not found"
            def json(self):
                return {"detail": "not found"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["webhook", "info", "999", "--server", "https://s.example.com", "--token", "tok"])
        assert ret != 0


class TestWebhookUpdateCLI:
    """Test webhook update command with mocked HTTP."""

    def test_update_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"id": 1, "url": "https://new.example.com"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def patch(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "update", "1", "--url", "https://new.example.com",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "updated" in out.lower()

    def test_update_nothing_specified(self, capsys, monkeypatch):
        """update with no options should error."""
        ret = main([
            "webhook", "update", "1",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret != 0
        combined = capsys.readouterr()
        assert "nothing to update" in (combined.out + combined.err).lower()


class TestWebhookDeleteCLI:
    """Test webhook delete command with mocked HTTP."""

    def test_delete_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = '{"ok": true}'
            def json(self):
                return {"ok": True}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "delete", "7",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "deleted" in out.lower()

    def test_delete_not_found(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 404
            text = "not found"
            def json(self):
                return {"detail": "not found"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "delete", "999",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret != 0


class TestWebhookTestCLI:
    """Test webhook test command with mocked HTTP."""

    def test_test_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"ok": True, "status_code": 200, "webhook_id": 3}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "test", "3",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "200" in out

    def test_test_delivery_failure(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 502
            text = "delivery failed"
            def json(self):
                return {"detail": "delivery failed"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "webhook", "test", "3",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret != 0


# ── builder CLI tests ──────────────────────────────────────────


class TestBuilderCLIHelp:
    """Verify builder subcommands appear in help."""

    @pytest.mark.parametrize("subcmd", ["list", "status", "run", "stop"])
    def test_builder_subcommand_help(self, subcmd, capsys):
        ret = main(["builder", subcmd, "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--token" in out

    def test_builder_group_lists_subcommands(self, capsys):
        ret = main(["builder", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        for cmd in ("list", "status", "run", "stop"):
            assert cmd in out


class TestBuilderListCLI:
    """Test builder list command with mocked HTTP."""

    def test_list_shows_builders(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "total": 1,
                    "builders": [{
                        "id": 1, "name": "catx-01", "platform": "linux",
                        "arch": "x86_64", "status": "online",
                        "current_jobs": 0, "max_jobs": 4,
                    }],
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builder", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "catx-01" in out
        assert "linux" in out

    def test_list_empty(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"total": 0, "builders": []}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builder", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no builders" in out.lower()


class TestBuilderStatusCLI:
    """Test builder status command with mocked HTTP."""

    def test_status_shows_details(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "id": 1, "name": "catx-01", "platform": "linux",
                    "arch": "x86_64", "status": "online", "org_slug": "",
                    "current_jobs": 2, "max_jobs": 8,
                    "labels": ["fast", "gpu"], "prefer_affinity": True,
                    "last_heartbeat": "2026-06-01T00:00:00", "created_at": "2026-05-31T12:00:00",
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builder", "status", "1", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "catx-01" in out
        assert "online" in out
        assert "fast" in out

    def test_status_not_found(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 404
            text = "not found"
            def json(self):
                return {"detail": "not found"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builder", "status", "999", "--server", "https://s.example.com", "--token", "tok"])
        assert ret != 0


class TestBuilderStopCLI:
    """Test builder stop command with mocked HTTP."""

    def test_stop_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"message": "builder unregistered", "id": 5}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builder", "stop", "5", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "unregistered" in out.lower()


# ── builds CLI tests ───────────────────────────────────────────


class TestBuildsCLIHelp:
    """Verify builds subcommands appear in help."""

    @pytest.mark.parametrize("subcmd", [
        "list", "info", "cancel", "cancel-dag", "log", "log-delete",
        "submit", "submit-dag", "purge",
    ])
    def test_builds_subcommand_help(self, subcmd, capsys):
        ret = main(["builds", subcmd, "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "--server" in out
        assert "--token" in out

    def test_builds_group_lists_subcommands(self, capsys):
        ret = main(["builds", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        for cmd in ("list", "info", "cancel", "log", "submit", "purge"):
            assert cmd in out


class TestBuildsListCLI:
    """Test builds list command with mocked HTTP."""

    def test_list_shows_jobs(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "total": 1,
                    "jobs": [{
                        "id": 10, "recipe_name": "zlib", "platform": "linux",
                        "config": "release", "link": "shared", "status": "pending",
                        "dag_id": None,
                    }],
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out
        assert "pending" in out

    def test_list_empty(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"total": 0, "jobs": []}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "list", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no build jobs" in out.lower()


class TestBuildsInfoCLI:
    """Test builds info command with mocked HTTP."""

    def test_info_shows_details(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "id": 10, "recipe_name": "zlib", "recipe_version": "1.3.1",
                    "platform": "linux", "arch": "x86_64", "config": "release",
                    "link": "shared", "status": "succeeded", "dag_id": "d1",
                    "builder_id": 3, "priority": 5,
                    "submitted_at": "2026-06-01T00:00:00",
                    "started_at": "2026-06-01T00:01:00",
                    "finished_at": "2026-06-01T00:05:00",
                    "error_message": None, "result_archive_url": "https://s.example.com/v1/download/zlib.tar.gz",
                    "depends_on": [8, 9],
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "info", "10", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "zlib" in out
        assert "succeeded" in out
        assert "8, 9" in out


class TestBuildsCancelCLI:
    """Test builds cancel command with mocked HTTP."""

    def test_cancel_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"message": "job cancelled", "id": 10, "status": "cancelled"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "cancel", "10", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "cancelled" in out.lower()


class TestBuildsCancelDagCLI:
    """Test builds cancel-dag command with mocked HTTP."""

    def test_cancel_dag_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"message": "dag cancelled", "dag_id": "d1", "cancelled": 3}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "cancel-dag", "d1", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "3" in out
        assert "cancelled" in out.lower()


class TestBuildsLogCLI:
    """Test builds log command with mocked HTTP."""

    def test_log_download(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = "build output line 1\nbuild output line 2\n"
            def json(self):
                return {}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "log", "10", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "build output line 1" in out

    def test_log_not_found(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 404
            text = "not found"
            def json(self):
                return {"detail": "not found"}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def get(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "log", "999", "--server", "https://s.example.com", "--token", "tok"])
        assert ret != 0


class TestBuildsLogDeleteCLI:
    """Test builds log-delete command with mocked HTTP."""

    def test_log_delete_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"ok": True, "job_id": 10}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "log-delete", "10", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "deleted" in out.lower()


class TestBuildsSubmitCLI:
    """Test builds submit command with mocked HTTP."""

    def test_submit_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "id": 42, "recipe_name": "zlib", "status": "pending",
                    "platform": "linux", "arch": "x86_64",
                    "config": "release", "link": "shared", "dag_id": None,
                }

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "builds", "submit",
            "--recipe", "zlib", "--platform", "linux", "--arch", "x86_64",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "42" in out
        assert "zlib" in out
        assert "pending" in out.lower()


class TestBuildsSubmitDagCLI:
    """Test builds submit-dag command with mocked HTTP."""

    def test_submit_dag_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"dag_id": "dag-001", "total": 2, "jobs": []}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "builds", "submit-dag",
            "--platform", "linux", "--arch", "x86_64",
            "--server", "https://s.example.com", "--token", "tok",
            "zlib", "boost",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "dag-001" in out.lower() or "2 jobs" in out


class TestBuildsPurgeCLI:
    """Test builds purge command with mocked HTTP."""

    def test_purge_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"ok": True, "purged": 5}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw): return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "builds", "purge", "--older-than", "30d",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "5" in out
        assert "purged" in out.lower()

    def test_purge_invalid_format(self, capsys, monkeypatch):
        """--older-than must match '<N>d' format."""
        ret = main([
            "builds", "purge", "--older-than", "2w",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret != 0

    def test_purge_with_delete_jobs(self, capsys, monkeypatch):
        """--delete-jobs should use the purge/builds endpoint."""
        urls_called = []

        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"ok": True, "purged": 3}

        class FakeClient:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def post(self, url, **kw):
                urls_called.append(url)
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main([
            "builds", "purge", "--older-than", "10d", "--delete-jobs",
            "--server", "https://s.example.com", "--token", "tok",
        ])
        assert ret == 0
        assert any("purge/builds" in u for u in urls_called)
