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


# ── push command ────────────────────────────────────────────────


class TestPushCommand:
    def test_push_help(self, capsys):
        ret = main(["push", "--help"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "push" in out.lower() or "upload" in out.lower()

    def test_push_file_backend(self, tmp_path, capsys):
        """Push an archive to a file:// destination."""
        archive = tmp_path / "test-1.0.tar.gz"
        archive.write_bytes(b"fake archive data")
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        ret = main(["push", str(archive), "--dest", f"file://{dest_dir}"])
        assert ret == 0
        assert (dest_dir / "test-1.0.tar.gz").exists()
        assert (dest_dir / "test-1.0.tar.gz").read_bytes() == b"fake archive data"

    def test_push_missing_file(self, tmp_path, capsys):
        ret = main(["push", str(tmp_path / "nonexistent.tar.gz"), "--dest", f"file://{tmp_path}"])
        assert ret == 1


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


@pytest.mark.parametrize("subcmd", ["push", "add", "remove", "world"])
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
            recipe_dir, *, platform, config, link, prefix, keep_build_dir, host_platform=""
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
            recipe_dir, *, platform, config, link, prefix, keep_build_dir, host_platform=""
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
        ret = main(["publish", "--help"])
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
