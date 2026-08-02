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
        "doctor",
    ],
)
def test_subcommand_help(subcmd):
    ret = main([subcmd, "--help"])
    assert ret == 0


# ── doctor command ──────────────────────────────────────────────


def test_doctor_runs_and_reports(capsys):
    """cvcpkg doctor prints a report and checks Python."""
    main(["doctor"])
    out = capsys.readouterr().out
    assert "cvcpkg doctor" in out
    assert "Python" in out
    assert "C/C++ compiler" in out


def test_doctor_python_check_ok():
    from cvcpkg.cli._doctor import _check_python

    c = _check_python()
    # The interpreter running the tests is >= 3.10.
    assert c.status == "ok"


def test_doctor_fails_when_required_tool_missing(capsys):
    """When CMake / compiler are absent, doctor exits non-zero."""
    from cvcpkg.cli import _doctor

    with mock.patch.object(_doctor.shutil, "which", return_value=None):
        ret = main(["doctor"])
    err = capsys.readouterr().err
    assert ret == 1
    assert "problem" in err.lower()


def test_doctor_server_unreachable(capsys):
    """--server with an unreachable host reports a failure."""
    from cvcpkg.cli._doctor import _check_server

    c = _check_server("http://127.0.0.1:59999")
    assert c.status == "fail"


def test_doctor_server_ok():
    """--server check parses a healthy /healthz response."""
    from cvcpkg.cli import _doctor

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"version": "2.0.0", "packages_count": 7}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    import httpx

    with mock.patch.object(httpx, "Client", _Client):
        c = _doctor._check_server("http://example.test")
    assert c.status == "ok"
    assert "2.0.0" in c.detail


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


def _make_org_catalog(tmp_path: Path) -> Path:
    """A catalog with the SAME name published under two different orgs, to
    test that an org-qualified spec ("cvc/libcvc") resolves to the right one
    and does not silently match — or silently miss — the wrong org."""
    catalog = {
        "schema_version": 1,
        "revision": 1,
        "bundles": [
            {
                "name": "libcvc",
                "org": "cvc",
                "version": "3.2.4+cvc.5",
                "upstream_version": "3.2.4",
                "cvc_revision": 5,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "cvcabc",
                "size_bytes": 100000,
                "archive_url": "",
                "source_release": "v1.1.0",
            },
            {
                "name": "libcvc",
                "org": "someone-else",
                "version": "9.9.9+cvc.1",
                "upstream_version": "9.9.9",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "otherabc",
                "size_bytes": 100000,
                "archive_url": "",
                "source_release": "v1.1.0",
            },
        ],
    }
    p = tmp_path / "org-catalog.yaml"
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

    def test_install_partial_miss_is_an_error(self, tmp_path, capsys):
        """Regression test: a resolvable + an unresolvable component together
        must NOT report success. Previously, `resolvable = [c for c in
        reqs.components if c.name in candidates or ...]` silently dropped any
        component with no catalog match, and the final check only fired when
        *everything* failed — so `cvcpkg install zlib typo-name` exited 0
        having installed only zlib, with no indication "typo-name" vanished.
        (Discovered via `cvcpkg install cvc/libcvc vtk`, which dropped
        "cvc/libcvc" for the same reason before org-qualified specs existed.)
        """
        cat = _make_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "zlib",
                    "typo-name",
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
        assert ret != 0
        captured = capsys.readouterr()
        assert "typo-name" in (captured.out + captured.err)

    def test_install_org_qualified_component_resolves(self, tmp_path, capsys):
        """cvcpkg install cvc/libcvc must actually install libcvc, scoped to
        the "cvc" org — not silently install nothing (the original bug)."""
        cat = _make_org_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "cvc/libcvc",
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
        out = capsys.readouterr().out
        assert "libcvc" in out

    def test_install_wrong_org_qualifier_is_an_error(self, tmp_path, capsys):
        """cvcpkg install other-org/libcvc must fail loudly (no candidate in
        that org), not silently succeed by matching a different org's libcvc."""
        cat = _make_org_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "other-org/libcvc",
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
        assert ret != 0


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

    def test_any_source_dep_built_as_any_into_build_prefix(self, tmp_path):
        """A `platform: any` source package consumed as a build dep must be
        built ONCE as `any` and into the BUILD prefix — never "for" the target.

        Regression (caught only by a real windows cross-build, not by the
        linux-only e2e): building it with platform=windows from a WSL host sent
        its build.sh to winhost delegation, which only runs .ps1 ->
        "winhost delegation only supports .ps1 build scripts, got build.sh".
        """
        recipes_dir = tmp_path / "recipes"
        # mysrc: a source package — platform-independent, files only
        self._make_recipe(recipes_dir, "mysrc", matrix=[{"platform": "any", "script": "build.sh"}])
        # app: the windows deliverable, consuming the source package as a BUILD dep
        self._make_recipe(
            recipes_dir,
            "app",
            matrix=[{"platform": "windows", "script": "build.ps1"}],
            deps=["mysrc"],
        )

        calls = []

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
            host_tools_prefix=None,
            build_prefix=None,
            incremental=False,
        ):
            calls.append((recipe_dir.name, platform, str(prefix)))
            return mock.MagicMock()

        pfx = tmp_path / "pfx"
        with (
            mock.patch("cvcpkg.builder.build_recipe", side_effect=mock_build_recipe),
            mock.patch("cvcpkg.platform.detect_platform", return_value="linux"),
        ):
            main(
                [
                    "build",
                    "app",
                    "--with-deps",
                    "--platform",
                    "windows",
                    "--prefix",
                    str(pfx),
                    "--local",
                    "--recipes-dir",
                    str(recipes_dir),
                    "--no-default-recipes",
                ]
            )

        by_name = {c[0]: c for c in calls}
        assert "mysrc" in by_name, f"source dep never built: {calls}"
        # THE bug: it must be built as `any`, not for the windows target.
        assert by_name["mysrc"][1] == "any", f"source pkg built as {by_name['mysrc'][1]!r}"
        # ...and staged into the build prefix, not the deliverable.
        assert by_name["mysrc"][2] == str(pfx) + ".build", by_name["mysrc"][2]
        # The deliverable itself still builds for the real target, into --prefix.
        assert by_name["app"][1] == "windows"
        assert by_name["app"][2] == str(pfx)

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
            host_tools_prefix=None,
            build_prefix=None,
            incremental=False,
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
                    "--with-deps",
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
            host_tools_prefix=None,
            build_prefix=None,
            incremental=False,
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
                    "--with-deps",
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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False),
            mock.patch(
                "cvcpkg.cli._publish._publish_simple", return_value="published"
            ) as mock_simple,
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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=True),
            mock.patch("cvcpkg.cli._publish._publish_simple") as mock_simple,
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

        with mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False):
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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False),
            mock.patch(
                "cvcpkg.cli._publish._publish_simple", return_value="published"
            ) as mock_simple,
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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False),
            mock.patch(
                "cvcpkg.cli._publish._publish_chunked", return_value="published"
            ) as mock_chunked,
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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False),
            mock.patch(
                "cvcpkg.cli._publish._publish_simple",
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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish._publish_simple", return_value="published"),
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
                    "",
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
                    "",
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
                    "",
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
                    "",
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
                    "",
                )
                is False
            )

    def test_variant_different_org_not_matched(self):
        """A stale public (org='') artifact must not block a publish to an org."""
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
                    "org": "",
                    "yanked": False,
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
                    "cvc-org",
                )
                is False
            )
        # The org must also be pushed down to the server as a query filter.
        assert mock_client.get.call_args.kwargs["params"]["org"] == "cvc-org"

    def test_variant_same_org_matched(self):
        """A live same-variant bundle in the same org is a match."""
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
                    "org": "cvc-org",
                    "yanked": False,
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
                    "cvc-org",
                )
                is True
            )

    def test_variant_yanked_not_matched(self):
        """A yanked same-variant bundle is treated as absent (must republish)."""
        from cvcpkg.cli import _variant_exists

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "packages": [
                {
                    "version": "3.2.4+cvc.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "build_type": "release",
                    "link": "shared",
                    "org": "",
                    "yanked": True,
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
                    "libcvc",
                    "3.2.4+cvc.1",
                    "linux",
                    "x86_64",
                    "release",
                    "shared",
                    "",
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


# ── admin CLI trio: server stats / server backup / builder logs ─


def _mock_httpx_client(get=None, post=None):
    client = mock.MagicMock()
    client.__enter__ = mock.MagicMock(return_value=client)
    client.__exit__ = mock.MagicMock(return_value=False)
    if get is not None:
        client.get.return_value = get
    if post is not None:
        client.post.return_value = post
    return client


class TestServerStatsCLI:
    def test_server_stats_prints_report(self, capsys):
        from cvcpkg.cli import _server

        stats = {
            "version": "2.0.0",
            "uptime_seconds": 12.3,
            "storage_scheme": "file",
            "mirror_mode": False,
            "database_enabled": True,
            "database_backend": "postgresql",
            "packages_count": 42,
            "total_storage_bytes": 2048,
            "orgs_count": 3,
            "builders_count": 2,
            "builders_connected": 1,
            "build_jobs_count": 7,
            "audit_entries": 99,
        }
        with mock.patch.object(_server, "_api_request", return_value=stats):
            ret = main(["server", "stats", "--server", "http://x", "--token", "t"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "postgresql" in out
        assert "42" in out
        assert "2.0 KiB" in out
        assert "2 (1 connected)" in out

    def test_human_bytes(self):
        from cvcpkg.cli._server import _human_bytes

        assert _human_bytes(0) == "0 B"
        assert _human_bytes(1024) == "1.0 KiB"
        assert _human_bytes(1536) == "1.5 KiB"


class TestServerBackupCLI:
    def test_server_backup_prints_result(self, capsys):
        from cvcpkg.cli import _server

        payload = {
            "message": "backup complete",
            "backend": "sqlite",
            "path": "/srv/data/backups/backup-20260709T000000Z.sqlite",
            "size_bytes": 4096,
        }
        with mock.patch.object(_server, "_api_request", return_value=payload):
            ret = main(["server", "backup", "--server", "http://x", "--token", "t"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "Backup complete" in out
        assert "sqlite" in out
        assert "4.0 KiB" in out


class TestBuilderLogsCLI:
    def test_builder_logs_lists_jobs(self, capsys):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "total": 2,
            "jobs": [
                {
                    "id": 5,
                    "recipe_name": "zlib",
                    "platform": "linux",
                    "arch": "x86_64",
                    "status": "succeeded",
                    "builder_id": 1,
                    "submitted_at": "2026-07-09T00:00:02Z",
                },
                {
                    "id": 4,
                    "recipe_name": "boost",
                    "platform": "linux",
                    "arch": "x86_64",
                    "status": "failed",
                    "builder_id": 1,
                    "submitted_at": "2026-07-09T00:00:01Z",
                },
            ],
        }
        with mock.patch("httpx.Client", return_value=_mock_httpx_client(get=resp)):
            ret = main(["builder", "logs", "1", "--server", "http://x", "--token", "t"])
        out = capsys.readouterr().out
        assert ret == 0
        # Newest job first.
        assert out.index("zlib") < out.index("boost")
        assert "builder #1" in out

    def test_builder_logs_empty(self, capsys):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"total": 0, "jobs": []}
        with mock.patch("httpx.Client", return_value=_mock_httpx_client(get=resp)):
            ret = main(["builder", "logs", "--server", "http://x", "--token", "t"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "No build jobs found" in out

    def test_builder_logs_tail(self, capsys):
        list_resp = mock.MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {
            "total": 1,
            "jobs": [
                {
                    "id": 9,
                    "recipe_name": "zlib",
                    "platform": "linux",
                    "arch": "x86_64",
                    "status": "running",
                    "builder_id": 2,
                    "submitted_at": "2026-07-09T00:00:00Z",
                }
            ],
        }
        log_resp = mock.MagicMock()
        log_resp.status_code = 200
        log_resp.text = "line1\nline2\nline3\nline4\n"

        client = _mock_httpx_client()
        client.get.side_effect = [list_resp, log_resp]
        with mock.patch("httpx.Client", return_value=client):
            ret = main(["builder", "logs", "--server", "http://x", "--token", "t", "--tail", "2"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "log tail: job #9" in out
        assert "line3" in out and "line4" in out
        assert "line1" not in out


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
            mock.patch("cvcpkg.cli._publish._variant_exists", return_value=False),
            mock.patch("cvcpkg.cli._publish._publish_simple", return_value="published"),
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

        monkeypatch.setattr("cvcpkg.cli._build._try_pull_server_recipes", mock_pull)

        recipes_dir = tmp_path / "empty_recipes"
        recipes_dir.mkdir()
        prefix = tmp_path / "prefix"
        main(
            [
                "install",
                "nonexistent-pkg-xyz",
                "--prefix",
                str(prefix),
                "--local",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
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

        monkeypatch.setattr("cvcpkg.cli._build._try_pull_server_recipes", mock_pull)

        # Use real recipes dir so recipe.yaml loads properly
        from cvcpkg.builder import find_recipes_dir

        try:
            real_recipes = str(find_recipes_dir())
        except Exception:
            pytest.skip("no recipes directory found")

        main(
            [
                "build",
                "zlib",
                "--local",
                "--no-deps",
                "--recipes-dir",
                real_recipes,
                "--prefix",
                str(tmp_path / "prefix"),
            ]
        )
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
            yaml.dump(
                {
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
                }
            )
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

        ret = main(
            [
                "recipe",
                "publish",
                "zlib",
                "--server",
                "https://test.example.com",
                "--token",
                "cvctok_test",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
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

        ret = main(
            [
                "recipe",
                "publish",
                "nonexistent",
                "--server",
                "https://test.example.com",
                "--token",
                "cvctok_test",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
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

        ret = main(
            [
                "recipe",
                "pull",
                "zlib",
                "--server",
                "https://test.example.com",
                "--output-dir",
                str(output_dir),
            ]
        )
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

        ret = main(
            [
                "recipe",
                "pull",
                "nonexistent",
                "--server",
                "https://test.example.com",
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
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

        ret = main(
            [
                "recipe",
                "pull-all",
                "--server",
                "https://test.example.com",
                "--output-dir",
                str(output_dir),
            ]
        )
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
                yaml.dump(
                    {
                        "recipe": {
                            "name": name,
                            "upstream_version": "1.0",
                            "platforms": ["linux"],
                            "deps": [],
                        },
                        "cvc_revision": 1,
                    }
                )
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

        ret = main(
            [
                "recipe",
                "push-all",
                "--server",
                "https://test.example.com",
                "--token",
                "cvctok_test",
                "--no-default-recipes",
                "--recipes-dir",
                str(recipes_dir),
            ]
        )
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
                        {
                            "name": "zlib",
                            "version": "1.3.1",
                            "bundle_size": 4096,
                            "updated_at": "2026-01-01",
                        },
                        {
                            "name": "boost",
                            "version": "1.85",
                            "bundle_size": 12000,
                            "updated_at": "2026-01-02",
                        },
                    ],
                }

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "recipe",
                "delete",
                "zlib",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "recipe",
                "delete",
                "nonexistent",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
                return {
                    "id": 42,
                    "url": "https://hook.example.com/cb",
                    "events": ["build.completed"],
                }

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "register",
                "https://hook.example.com/cb",
                "-e",
                "build.completed",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "register",
                "https://hook.example.com/cb",
                "-e",
                "build.completed",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
                        {
                            "id": 1,
                            "url": "https://h.example.com",
                            "events": ["build.completed"],
                            "active": True,
                        },
                    ],
                }

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["webhook", "info", "999", "--server", "https://s.example.com", "--token", "tok"]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def patch(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "update",
                "1",
                "--url",
                "https://new.example.com",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "updated" in out.lower()

    def test_update_nothing_specified(self, capsys, monkeypatch):
        """update with no options should error."""
        ret = main(
            [
                "webhook",
                "update",
                "1",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "delete",
                "7",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "delete",
                "999",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "test",
                "3",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "webhook",
                "test",
                "3",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
        assert ret != 0


# ── builder CLI tests ──────────────────────────────────────────


class TestBuilderCLIHelp:
    """Verify builder subcommands appear in help."""

    @pytest.mark.parametrize("subcmd", ["list", "status", "run", "unregister"])
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
        for cmd in ("list", "status", "run", "unregister"):
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
                    "builders": [
                        {
                            "id": 1,
                            "name": "catx-01",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "online",
                            "current_jobs": 0,
                            "max_jobs": 4,
                        }
                    ],
                }

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
                    "id": 1,
                    "name": "catx-01",
                    "platform": "linux",
                    "arch": "x86_64",
                    "status": "online",
                    "org_slug": "",
                    "current_jobs": 2,
                    "max_jobs": 8,
                    "labels": ["fast", "gpu"],
                    "prefer_affinity": True,
                    "last_heartbeat": "2026-06-01T00:00:00",
                    "created_at": "2026-05-31T12:00:00",
                }

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
        ret = main(
            ["builder", "status", "1", "--server", "https://s.example.com", "--token", "tok"]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builder", "status", "999", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret != 0


class TestBuilderUnregisterCLI:
    """Test builder unregister command with mocked HTTP."""

    def test_unregister_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"message": "builder unregistered", "id": 5}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builder", "unregister", "5", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "unregistered" in out.lower()


# ── builds CLI tests ───────────────────────────────────────────


class TestBuildsCLIHelp:
    """Verify builds subcommands appear in help."""

    @pytest.mark.parametrize(
        "subcmd",
        [
            "list",
            "info",
            "cancel",
            "cancel-dag",
            "pause",
            "resume",
            "pause-dag",
            "resume-dag",
            "log",
            "log-delete",
            "submit",
            "submit-dag",
            "follow-dag",
            "monitor",
            "purge",
        ],
    )
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
        for cmd in (
            "list",
            "info",
            "cancel",
            "pause",
            "resume",
            "log",
            "submit",
            "follow-dag",
            "monitor",
            "purge",
        ):
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
                    "jobs": [
                        {
                            "id": 10,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "config": "release",
                            "link": "shared",
                            "status": "pending",
                            "dag_id": None,
                        }
                    ],
                }

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
                    "id": 10,
                    "recipe_name": "zlib",
                    "recipe_version": "1.3.1",
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "status": "succeeded",
                    "dag_id": "d1",
                    "builder_id": 3,
                    "priority": 5,
                    "submitted_at": "2026-06-01T00:00:00",
                    "started_at": "2026-06-01T00:01:00",
                    "finished_at": "2026-06-01T00:05:00",
                    "error_message": None,
                    "result_archive_url": "https://s.example.com/v1/download/zlib.tar.gz",
                    "depends_on": [8, 9],
                }

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "cancel", "10", "--server", "https://s.example.com", "--token", "tok"]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "cancel-dag", "d1", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "3" in out
        assert "cancelled" in out.lower()


class TestBuildsPauseCLI:
    """Test builds pause command with mocked HTTP."""

    def test_pause_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"message": "job paused", "id": 10, "status": "paused"}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(["builds", "pause", "10", "--server", "https://s.example.com", "--token", "tok"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "paused" in out.lower()


class TestBuildsResumeCLI:
    """Test builds resume command with mocked HTTP."""

    def test_resume_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"message": "job resumed", "id": 10, "status": "pending"}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "resume", "10", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "pending" in out.lower()


class TestBuildsPauseDagCLI:
    """Test builds pause-dag command with mocked HTTP."""

    def test_pause_dag_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"message": "dag paused", "dag_id": "d1", "paused": 3}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "pause-dag", "d1", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "3" in out
        assert "paused" in out.lower()


class TestBuildsResumeDagCLI:
    """Test builds resume-dag command with mocked HTTP."""

    def test_resume_dag_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"message": "dag resumed", "dag_id": "d1", "resumed": 3}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "resume-dag", "d1", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "3" in out
        assert "resumed" in out.lower()


class TestBuildsFollowDagCLI:
    """Test builds follow-dag command with mocked HTTP."""

    def test_follow_dag_all_succeeded(self, capsys, monkeypatch):
        """follow-dag exits 0 when all jobs succeed."""
        import itertools

        call_count = itertools.count()

        class FakeResponse:
            status_code = 200

            def json(self):
                n = next(call_count)
                if n == 0:
                    # First poll: one job running
                    return {
                        "jobs": [
                            {
                                "id": 1,
                                "recipe_name": "zlib",
                                "platform": "linux",
                                "arch": "x86_64",
                                "status": "running",
                                "builder_id": None,
                            }
                        ]
                    }
                # Second poll: job succeeded
                return {
                    "jobs": [
                        {
                            "id": 1,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                            "builder_id": None,
                        }
                    ]
                }

        class FakeStreamResponse:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def iter_lines(self):
                yield "data: building..."
                yield "event: done"

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

            def stream(self, method, url, **kw):
                return FakeStreamResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "follow-dag", "dag1", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "1/1 succeeded" in out

    def test_follow_dag_with_failure_exits_1(self, capsys, monkeypatch):
        """follow-dag exits 1 when any job fails."""

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "jobs": [
                        {
                            "id": 1,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                            "builder_id": None,
                        },
                        {
                            "id": 2,
                            "recipe_name": "boost",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "failed",
                            "builder_id": None,
                        },
                    ]
                }

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
        ret = main(
            ["builds", "follow-dag", "dag1", "--server", "https://s.example.com", "--token", "tok"]
        )
        assert ret == 1


class TestBuildsLogCLI:
    """Test builds log command with mocked HTTP."""

    def test_log_download(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = "build output line 1\nbuild output line 2\n"

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse()

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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            ["builds", "log-delete", "10", "--server", "https://s.example.com", "--token", "tok"]
        )
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
                    "id": 42,
                    "recipe_name": "zlib",
                    "status": "pending",
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "dag_id": None,
                }

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit",
                "--recipe",
                "zlib",
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit-dag",
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "zlib",
                "boost",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "dag-001" in out.lower() or "2 jobs" in out

    def test_submit_dag_skips_unschedulable_combos(self, capsys, monkeypatch):
        """Combos no registered builder can serve are skipped, not submitted."""
        posted: list[str] = []

        class FakeResp:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                # Builder registry advertises only linux/x86_64.
                return FakeResp(
                    {"builders": [{"platform": "linux", "arch": "x86_64", "capabilities": {}}]}
                )

            def post(self, url, **kw):
                posted.append(url)
                return FakeResp({"dag_id": "dag-x", "total": 1, "jobs": []})

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit-dag",
                "--platform",
                "freebsd,netbsd",
                "--arch",
                "arm64",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "nasm",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "Skipping freebsd/arm64" in out
        assert "Skipping netbsd/arm64" in out
        # No builder can serve any combo → nothing is submitted.
        assert posted == []

    def test_submit_dag_allow_unschedulable_skips_builder_check(self, capsys, monkeypatch):
        """--allow-unschedulable bypasses the builder registry lookup."""
        calls = {"get": 0}

        class FakeResp:
            status_code = 200
            text = ""

            def json(self):
                return {"dag_id": "dag-y", "total": 0, "jobs": []}

            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                # Only the builder-registry query is gated by
                # --allow-unschedulable; the /v1/packages query that powers
                # dependency auto-add is a separate call and always runs.
                if "/v1/builders" in url:
                    calls["get"] += 1
                return FakeResp()

            def post(self, url, **kw):
                return FakeResp()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit-dag",
                "--platform",
                "freebsd",
                "--arch",
                "arm64",
                "--allow-unschedulable",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "nasm",
            ]
        )
        assert ret == 0
        # Gate disabled → builder registry is never consulted.
        assert calls["get"] == 0


class TestBuildsPurgeCLI:
    """Test builds purge command with mocked HTTP."""

    def test_purge_success(self, capsys, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"ok": True, "purged": 5}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "purge",
                "--older-than",
                "30d",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "5" in out
        assert "purged" in out.lower()

    def test_purge_invalid_format(self, capsys, monkeypatch):
        """--older-than must match '<N>d' format."""
        ret = main(
            [
                "builds",
                "purge",
                "--older-than",
                "2w",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
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
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                urls_called.append(url)
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "purge",
                "--older-than",
                "10d",
                "--delete-jobs",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
        assert ret == 0
        assert any("purge/builds" in u for u in urls_called)


# ── Tests for _wait_for_jobs, _wait_for_dags, builds monitor, --wait ──


class TestWaitForJobs:
    """Test the _wait_for_jobs helper function."""

    def test_all_succeed(self, capsys, monkeypatch):
        """Jobs that succeed immediately produce success message."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = {"n": 0}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "id": 1,
                    "recipe_name": "zlib",
                    "platform": "linux",
                    "status": "succeeded",
                }

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                call_count["n"] += 1
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        from cvcpkg.cli import _wait_for_jobs

        _wait_for_jobs("https://s.example.com", "tok", [1])
        out = capsys.readouterr().out
        assert "succeeded" in out
        assert "All 1 job(s) succeeded" in out

    def test_job_fails_raises(self, capsys, monkeypatch):
        """A failed job raises ClickException."""
        monkeypatch.setattr("time.sleep", lambda _: None)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "id": 5,
                    "recipe_name": "boost",
                    "platform": "freebsd",
                    "status": "failed",
                }

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

        import click as _click

        from cvcpkg.cli import _wait_for_jobs

        with pytest.raises(_click.ClickException, match="failed"):
            _wait_for_jobs("https://s.example.com", "tok", [5])

    def test_polls_until_terminal(self, capsys, monkeypatch):
        """Jobs transition from running to succeeded across polls."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        poll_count = {"n": 0}

        class FakeResponse:
            status_code = 200

            def __init__(self):
                poll_count["n"] += 1

            def json(self):
                if poll_count["n"] <= 2:
                    return {
                        "id": 10,
                        "recipe_name": "curl",
                        "platform": "linux",
                        "status": "running",
                    }
                return {
                    "id": 10,
                    "recipe_name": "curl",
                    "platform": "linux",
                    "status": "succeeded",
                }

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

        from cvcpkg.cli import _wait_for_jobs

        _wait_for_jobs("https://s.example.com", "tok", [10])
        out = capsys.readouterr().out
        assert "All 1 job(s) succeeded" in out
        assert poll_count["n"] > 2


class TestWaitForDags:
    """Test the _wait_for_dags helper function."""

    def test_dag_all_succeed(self, capsys, monkeypatch):
        """All DAG jobs succeeding produces success message."""
        monkeypatch.setattr("time.sleep", lambda _: None)

        class FakeListResponse:
            status_code = 200

            def json(self):
                return {
                    "total": 2,
                    "jobs": [
                        {
                            "id": 1,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                        },
                        {
                            "id": 2,
                            "recipe_name": "boost",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                        },
                    ],
                }

        class FakeInfoResponse:
            status_code = 200

            def __init__(self, jid):
                self._jid = jid

            def json(self):
                return {"id": self._jid, "status": "succeeded"}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                if "/v1/builds/" in url and not url.endswith("/builds"):
                    # Individual job fetch
                    jid = int(url.rstrip("/").split("/")[-1])
                    return FakeInfoResponse(jid)
                return FakeListResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        from cvcpkg.cli import _wait_for_dags

        _wait_for_dags("https://s.example.com", "tok", ["dag-001"])
        out = capsys.readouterr().out
        assert "All 2 job(s) succeeded" in out

    def test_dag_with_failure_raises(self, capsys, monkeypatch):
        """A failed job in the DAG raises ClickException."""
        monkeypatch.setattr("time.sleep", lambda _: None)

        class FakeListResponse:
            status_code = 200

            def json(self):
                return {
                    "total": 2,
                    "jobs": [
                        {
                            "id": 1,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                        },
                        {
                            "id": 2,
                            "recipe_name": "boost",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "failed",
                        },
                    ],
                }

        class FakeInfoResponse:
            status_code = 200

            def __init__(self, jid):
                self._jid = jid

            def json(self):
                status = "succeeded" if self._jid == 1 else "failed"
                return {"id": self._jid, "status": status}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                if "/v1/builds/" in url and not url.endswith("/builds"):
                    jid = int(url.rstrip("/").split("/")[-1])
                    return FakeInfoResponse(jid)
                return FakeListResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)

        import click as _click

        from cvcpkg.cli import _wait_for_dags

        with pytest.raises(_click.ClickException, match="did not succeed"):
            _wait_for_dags("https://s.example.com", "tok", ["dag-fail"])


class TestBuildsSubmitWait:
    """Test builds submit --wait end-to-end via CLI."""

    def test_submit_with_wait_success(self, capsys, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = {"n": 0}

        class FakeResponse:
            status_code = 200

            def json(self):
                call_count["n"] += 1
                # First call is the POST to submit
                if call_count["n"] == 1:
                    return {
                        "id": 99,
                        "recipe_name": "openssl",
                        "status": "pending",
                        "platform": "linux",
                        "arch": "x86_64",
                        "config": "release",
                        "link": "shared",
                        "dag_id": None,
                    }
                # Subsequent GETs for poll
                return {
                    "id": 99,
                    "recipe_name": "openssl",
                    "platform": "linux",
                    "status": "succeeded",
                }

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit",
                "--recipe",
                "openssl",
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "--wait",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "99" in out
        assert "succeeded" in out

    def test_submit_with_wait_failure(self, capsys, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = {"n": 0}

        class FakeResponse:
            status_code = 200

            def json(self):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return {
                        "id": 100,
                        "recipe_name": "boost",
                        "status": "pending",
                        "platform": "linux",
                        "arch": "x86_64",
                        "config": "release",
                        "link": "shared",
                        "dag_id": None,
                    }
                return {
                    "id": 100,
                    "recipe_name": "boost",
                    "platform": "linux",
                    "status": "failed",
                }

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

            def get(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit",
                "--recipe",
                "boost",
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "--wait",
            ]
        )
        assert ret == 1


class TestBuildsSubmitDagWait:
    """Test builds submit-dag --wait end-to-end via CLI."""

    def test_submit_dag_with_wait_success(self, capsys, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = {"n": 0}

        class FakeResponse:
            status_code = 200

            def json(self):
                call_count["n"] += 1
                # First call is POST to create DAG
                if call_count["n"] == 1:
                    return {"dag_id": "dag-wait-001", "total": 2, "jobs": []}
                # Subsequent calls are GET to list/check jobs
                return {
                    "total": 2,
                    "jobs": [
                        {
                            "id": 1,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                        },
                        {
                            "id": 2,
                            "recipe_name": "boost",
                            "platform": "linux",
                            "arch": "x86_64",
                            "status": "succeeded",
                        },
                    ],
                }

        class FakeInfoResponse:
            status_code = 200

            def __init__(self, jid):
                self._jid = jid

            def json(self):
                return {"id": self._jid, "status": "succeeded"}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, **kw):
                return FakeResponse()

            def get(self, url, **kw):
                # If fetching individual job info
                if "/v1/builds/" in url and not url.rstrip("/").endswith("builds"):
                    # Extract job ID from URL
                    try:
                        jid = int(url.rstrip("/").split("/")[-1])
                    except ValueError:
                        jid = 1
                    return FakeInfoResponse(jid)
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "submit-dag",
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "--wait",
                "zlib",
                "boost",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "dag-wait-001" in out.lower()
        assert "succeeded" in out


class TestBuildsMonitorCLI:
    """Test builds monitor command with mocked HTTP."""

    def test_monitor_renders_output(self, capsys, monkeypatch):
        """Monitor fetches data, renders once, then KeyboardInterrupt exits."""
        monkeypatch.setattr("time.sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
        monkeypatch.setattr("shutil.get_terminal_size", lambda *a, **k: os.terminal_size((80, 24)))

        builders_data = {
            "total": 2,
            "builders": [
                {
                    "id": 1,
                    "name": "star-00",
                    "platform": "linux",
                    "arch": "x86_64",
                    "status": "online",
                    "current_jobs": 1,
                    "max_jobs": 4,
                },
                {
                    "id": 2,
                    "name": "freebsd-build",
                    "platform": "freebsd",
                    "arch": "x86_64",
                    "status": "offline",
                    "current_jobs": 0,
                    "max_jobs": 2,
                },
            ],
        }
        jobs_data = {
            "total": 3,
            "jobs": [
                {
                    "id": 10,
                    "recipe_name": "zlib",
                    "platform": "linux",
                    "status": "running",
                    "builder_name": "star-00",
                },
                {
                    "id": 11,
                    "recipe_name": "boost",
                    "platform": "linux",
                    "status": "succeeded",
                    "builder_name": "star-00",
                },
                {
                    "id": 12,
                    "recipe_name": "curl",
                    "platform": "freebsd",
                    "status": "failed",
                    "builder_name": "freebsd-build",
                },
            ],
        }

        class FakeResponse:
            status_code = 200

            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                if "/v1/builders" in url:
                    return FakeResponse(builders_data)
                return FakeResponse(jobs_data)

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "monitor",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "--interval",
                "1",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "star-00" in out
        assert "freebsd-build" in out
        assert "1 online" in out
        assert "1 offline" in out
        assert "zlib" in out
        assert "Monitor stopped" in out

    def test_monitor_with_dag_filter(self, capsys, monkeypatch):
        """Monitor passes --dag-id filter to jobs endpoint."""
        monkeypatch.setattr("time.sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
        monkeypatch.setattr("shutil.get_terminal_size", lambda *a, **k: os.terminal_size((80, 24)))
        captured_params: list[dict] = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"total": 0, "builders": [], "jobs": []}

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                if "params" in kw:
                    captured_params.append(kw["params"])
                return FakeResponse()

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "monitor",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
                "--dag-id",
                "my-dag-123",
            ]
        )
        assert ret == 0
        # Verify dag_id was passed as parameter
        assert any("my-dag-123" in str(p) for p in captured_params)

    def test_monitor_handles_server_errors(self, capsys, monkeypatch):
        """Monitor gracefully handles server errors (returns empty data)."""
        monkeypatch.setattr("time.sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
        monkeypatch.setattr("shutil.get_terminal_size", lambda *a, **k: os.terminal_size((80, 24)))

        class FakeResponse:
            status_code = 500
            text = "Internal Server Error"

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
        ret = main(
            [
                "builds",
                "monitor",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "0 online" in out
        assert "No active jobs" in out

    def test_monitor_no_active_jobs(self, capsys, monkeypatch):
        """Monitor shows 'No active jobs' when only completed jobs exist."""
        monkeypatch.setattr("time.sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
        monkeypatch.setattr("shutil.get_terminal_size", lambda *a, **k: os.terminal_size((80, 24)))

        class FakeResponse:
            status_code = 200

            def __init__(self, url):
                self._url = url

            def json(self):
                if "builders" in self._url:
                    return {
                        "total": 1,
                        "builders": [
                            {
                                "name": "b1",
                                "platform": "linux",
                                "arch": "x86_64",
                                "status": "online",
                                "current_jobs": 0,
                                "max_jobs": 4,
                            }
                        ],
                    }
                return {
                    "total": 1,
                    "jobs": [
                        {
                            "id": 1,
                            "recipe_name": "zlib",
                            "platform": "linux",
                            "status": "succeeded",
                        }
                    ],
                }

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResponse(url)

        monkeypatch.setattr("httpx.Client", FakeClient)
        ret = main(
            [
                "builds",
                "monitor",
                "--server",
                "https://s.example.com",
                "--token",
                "tok",
            ]
        )
        assert ret == 0
        out = capsys.readouterr().out
        assert "No active jobs" in out
        assert "Recent completed" in out
        assert "zlib" in out


# ── Conflict detection ───────────────────────────────────────────


def _make_conflict_catalog(tmp_path: Path) -> Path:
    """Catalog with python313 and python313t entries for conflict testing."""
    catalog = {
        "schema_version": 1,
        "revision": 1,
        "bundles": [
            {
                "name": "python313",
                "version": "3.13.3+cvc.1",
                "upstream_version": "3.13.3",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "aaa111",
                "size_bytes": 20000000,
                "archive_url": "",
                "source_release": "v3.13.3",
            },
            {
                "name": "python313t",
                "version": "3.13.3+cvc.1",
                "upstream_version": "3.13.3",
                "cvc_revision": 1,
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "bbb222",
                "size_bytes": 20000000,
                "archive_url": "",
                "source_release": "v3.13.3",
            },
        ],
    }
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.dump(catalog, default_flow_style=False))
    return p


def _make_recipes_dir(tmp_path: Path) -> Path:
    """Recipes dir with python313 and python313t declaring mutual conflicts."""
    import yaml as _yaml

    def _recipe(name: str, conflicts: list) -> dict:
        d = {
            "schema_version": 1,
            "recipe": {
                "name": name,
                "upstream_version": "3.13.3",
                "cvc_revision": 1,
            },
            "source": {"type": "vendored", "path": f"third-party/{name}"},
            "patches": [],
            "build": {
                "matrix": [{"platform": "linux", "script": "build.sh"}],
            },
            "package": {"files": ["lib/*", "include/*"], "cmake_packages": []},
        }
        if conflicts:
            d["conflicts"] = conflicts
        return d

    rd = tmp_path / "recipes"
    for name, confs in [("python313", ["python313t"]), ("python313t", ["python313"])]:
        pkg_dir = rd / name
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "recipe.yaml").write_text(
            _yaml.dump(_recipe(name, confs), default_flow_style=False)
        )
    return rd


def _write_lockfile(prefix: Path, bundles: list[str]) -> None:
    """Write a minimal lockfile with the given bundle names pre-installed."""
    from cvcpkg.lockfile import LockEntry, Lockfile

    lock = Lockfile(
        platform="linux",
        arch="x86_64",
        config="release",
        link="shared",
        bundles=[
            LockEntry(name=b, version="3.13.3+cvc.1", upstream_version="3.13.3") for b in bundles
        ],
    )
    lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


class TestCheckConflicts:
    """Direct unit tests for cvcpkg.cli._install._check_conflicts."""

    def _fn(self):
        from cvcpkg.cli._install import _check_conflicts

        return _check_conflicts

    def test_no_recipe_dirs_is_noop(self, tmp_path):
        self._fn()(["python313", "python313t"], tmp_path, None)

    def test_empty_installing_is_noop(self, tmp_path):
        rd = _make_recipes_dir(tmp_path)
        self._fn()([], tmp_path, [rd])

    def test_no_conflict_packages_passes(self, tmp_path):
        rd = _make_recipes_dir(tmp_path)
        # zlib has no recipe → no conflicts
        self._fn()(["zlib"], tmp_path, [rd])

    def test_co_install_conflict_raises(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313", "python313t"], tmp_path, [rd])
        msg = exc_info.value.format_message()
        assert "python313" in msg
        assert "python313t" in msg
        assert "conflicts" in msg.lower()

    def test_installed_conflict_raises(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313t"], prefix, [rd])
        msg = exc_info.value.format_message()
        assert "python313t" in msg
        assert "python313" in msg
        assert "uninstall" in msg.lower()

    def test_installed_conflict_includes_prefix_hint(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "myprefix"
        _write_lockfile(prefix, ["python313"])
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313t"], prefix, [rd])
        msg = exc_info.value.format_message()
        assert str(prefix) in msg

    def test_no_conflict_when_only_one_installed(self, tmp_path):
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        # Installing python313 again (same package, no conflict with itself)
        self._fn()(["python313"], prefix, [rd])

    def test_no_conflict_when_lockfile_missing(self, tmp_path):
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        # prefix has no lockfile at all
        self._fn()(["python313t"], prefix, [rd])

    def test_corrupt_lockfile_silently_skipped(self, tmp_path):
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        lock_dir = prefix / "share" / "libcvc-deps"
        lock_dir.mkdir(parents=True)
        (lock_dir / "lockfile.yaml").write_text("not: valid: yaml: [{")
        # Should not raise even with corrupt lockfile
        self._fn()(["python313t"], prefix, [rd])


class TestInstallConflictGating:
    """Integration tests: install command raises on conflicting packages."""

    def test_co_install_blocked(self, tmp_path, capsys):
        """Installing python313 + python313t together must fail."""
        cat = _make_conflict_catalog(tmp_path)
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        ret = main(
            [
                "install",
                "python313",
                "python313t",
                "--catalog",
                str(cat),
                "--prefix",
                str(prefix),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(rd),
            ]
        )
        assert ret != 0
        err = capsys.readouterr().err
        assert "conflicts" in err.lower()

    def test_co_install_error_names_both_packages(self, tmp_path, capsys):
        cat = _make_conflict_catalog(tmp_path)
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        main(
            [
                "install",
                "python313",
                "python313t",
                "--catalog",
                str(cat),
                "--prefix",
                str(prefix),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(rd),
            ]
        )
        err = capsys.readouterr().err
        assert "python313" in err
        assert "python313t" in err

    def test_install_over_conflicting_installed_pkg_blocked(self, tmp_path, capsys):
        """Installing python313t when python313 is in lockfile must fail."""
        cat = _make_conflict_catalog(tmp_path)
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        ret = main(
            [
                "install",
                "python313t",
                "--catalog",
                str(cat),
                "--prefix",
                str(prefix),
                "--platform",
                "linux",
                "--arch",
                "x86_64",
                "--recipes-dir",
                str(rd),
            ]
        )
        assert ret != 0
        err = capsys.readouterr().err
        assert "python313t" in err
        assert "python313" in err
        assert "uninstall" in err.lower()

    def test_install_with_no_conflict_succeeds(self, tmp_path, capsys):
        """Installing only python313 (no conflict) proceeds normally."""
        cat = _make_conflict_catalog(tmp_path)
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "python313",
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                    "--recipes-dir",
                    str(rd),
                ]
            )
        assert ret == 0

    def test_install_without_recipes_dir_uses_default_recipes(self, tmp_path, capsys):
        """Without --recipes-dir the DEFAULT (bundled / cwd-overlay) recipes
        still feed the conflict check — declared exclusions like
        python313 vs python313t (or pytest-cp311 vs pytest-cp313 sharing the
        `pytest` provides slot) are enforced on plain installs, not only when
        the caller passes --recipes-dir. Degrades to a skip only when no
        recipe dir can be found at all."""
        cat = _make_conflict_catalog(tmp_path)
        prefix = tmp_path / "prefix"
        with mock.patch("cvcpkg.installer.install_entry"):
            ret = main(
                [
                    "install",
                    "python313",
                    "python313t",
                    "--catalog",
                    str(cat),
                    "--prefix",
                    str(prefix),
                    "--platform",
                    "linux",
                    "--arch",
                    "x86_64",
                    # no --recipes-dir: defaults resolve, conflict fires
                ]
            )
        assert ret != 0
        assert "conflict" in capsys.readouterr().err.lower()


class TestConflictErrorMessages:
    """Verify exact content of conflict error messages."""

    def _fn(self):
        from cvcpkg.cli._install import _check_conflicts

        return _check_conflicts

    def test_co_install_message_says_cannot_install_both(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313", "python313t"], tmp_path, [rd])
        msg = exc_info.value.format_message()
        assert "cannot install both" in msg.lower()

    def test_co_install_message_says_remove_from_request(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313", "python313t"], tmp_path, [rd])
        msg = exc_info.value.format_message()
        assert "remove" in msg.lower() or "retry" in msg.lower()

    def test_installed_conflict_message_says_uninstall(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313t"], prefix, [rd])
        msg = exc_info.value.format_message()
        assert "uninstall" in msg.lower()

    def test_installed_conflict_message_says_then_retry(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313t"], prefix, [rd])
        msg = exc_info.value.format_message()
        assert "retry" in msg.lower()

    def test_installed_conflict_message_includes_cvcpkg_uninstall_command(self, tmp_path):
        import click

        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313t"], prefix, [rd])
        msg = exc_info.value.format_message()
        # The message should contain an actionable CLI command the user can copy
        assert "cvcpkg uninstall python313" in msg


class TestAsymmetricConflicts:
    """Conflict checks are only enforced when the *installing* package declares
    the conflict — not the already-installed one.  This is by design: the
    conflict list is the installed package's way of saying "I can't share a
    prefix with X".  Both sides should declare the conflict for full safety."""

    def _fn(self):
        from cvcpkg.cli._install import _check_conflicts

        return _check_conflicts

    def _make_one_sided_recipes(self, tmp_path: Path) -> Path:
        """python313t declares conflict, python313 does NOT."""
        import yaml as _yaml

        rd = tmp_path / "recipes"
        for name, confs in [("python313", []), ("python313t", ["python313"])]:
            pkg_dir = rd / name
            pkg_dir.mkdir(parents=True, exist_ok=True)
            d = {
                "schema_version": 1,
                "recipe": {
                    "name": name,
                    "upstream_version": "3.13.3",
                    "cvc_revision": 1,
                },
                "source": {"type": "vendored", "path": f"third-party/{name}"},
                "patches": [],
                "build": {
                    "matrix": [{"platform": "linux", "script": "build.sh"}],
                },
                "package": {
                    "files": ["lib/*", "include/*"],
                    "cmake_packages": [],
                },
            }
            if confs:
                d["conflicts"] = confs
            (pkg_dir / "recipe.yaml").write_text(_yaml.dump(d, default_flow_style=False))
        return rd

    def test_co_install_caught_from_declaring_side(self, tmp_path):
        """python313t declares conflict → caught when both are co-installed."""
        import click

        rd = self._make_one_sided_recipes(tmp_path)
        with pytest.raises(click.ClickException) as exc_info:
            self._fn()(["python313", "python313t"], tmp_path, [rd])
        assert "python313" in exc_info.value.format_message()

    def test_installing_non_declaring_package_not_caught(self, tmp_path):
        """python313 does NOT declare conflicts, so installing it over python313t
        is not blocked.  This is expected behaviour — both sides should declare
        the conflict for full safety."""
        rd = self._make_one_sided_recipes(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313t"])
        # python313 has no conflicts → no ClickException
        self._fn()(["python313"], prefix, [rd])  # must not raise

    def test_installing_declaring_package_is_still_caught(self, tmp_path):
        """python313t declares conflict → caught when installed over python313."""
        import click

        rd = self._make_one_sided_recipes(tmp_path)
        prefix = tmp_path / "prefix"
        _write_lockfile(prefix, ["python313"])
        with pytest.raises(click.ClickException):
            self._fn()(["python313t"], prefix, [rd])


class TestInstallConflictWithRequirementsFile:
    """Conflict check fires when packages come from a requirements file."""

    def test_conflict_via_from_requirements_file(self, tmp_path, capsys):
        cat = _make_conflict_catalog(tmp_path)
        rd = _make_recipes_dir(tmp_path)
        prefix = tmp_path / "prefix"
        req_file = tmp_path / "requirements.yaml"
        import yaml as _yaml

        req_file.write_text(
            _yaml.dump(
                {
                    "platform": "linux",
                    "arch": "x86_64",
                    "config": "release",
                    "link": "shared",
                    "components": ["python313", "python313t"],
                }
            )
        )
        ret = main(
            [
                "install",
                "--from",
                str(req_file),
                "--catalog",
                str(cat),
                "--prefix",
                str(prefix),
                "--recipes-dir",
                str(rd),
            ]
        )
        assert ret != 0
        err = capsys.readouterr().err
        assert "python313" in err
        assert "python313t" in err


# ── _wait_for_dags unschedulable handling (regression) ──────────


class TestWaitForDagsUnschedulable:
    """submit-dag --wait must terminate when the server reaps jobs as
    unschedulable.  Regression for the pr-recipe-build-dev hangs (dags
    pr-223 / pr-226): "unschedulable" was missing from the wait loops'
    terminal-state set, so a DAG containing platforms no dev builder
    serves polled forever and wedged the CI runner until the workflow
    timeout."""

    def _mock_client(self, statuses):
        """Client serving a one-DAG /v1/builds listing + per-job detail."""
        jobs = [
            {"id": jid, "status": st, "recipe_name": "r", "platform": "p", "arch": "x"}
            for jid, st in statuses.items()
        ]

        def _get(url, headers=None, params=None):
            resp = mock.MagicMock()
            resp.status_code = 200
            if url.endswith("/v1/builds"):
                resp.json.return_value = {"jobs": jobs}
            else:  # /v1/builds/<id>
                jid = int(url.rsplit("/", 1)[1])
                resp.json.return_value = {"id": jid, "status": statuses[jid]}
            return resp

        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(return_value=client)
        client.__exit__ = mock.MagicMock(return_value=False)
        client.get.side_effect = _get
        return client

    def _bounded_sleep(self):
        """A time.sleep stand-in that fails the test instead of hanging
        it if the wait loop stops treating the statuses as terminal."""
        calls = {"n": 0}

        def _sleep(_seconds):
            calls["n"] += 1
            if calls["n"] > 10:
                raise AssertionError("wait loop did not terminate")

        return _sleep

    def test_unschedulable_is_terminal_and_fails_strict_wait(self):
        from cvcpkg.cli._builds import _wait_for_dags

        client = self._mock_client({1: "succeeded", 2: "unschedulable"})
        with (
            mock.patch("httpx.Client", return_value=client),
            mock.patch("cvcpkg.cli._builds.time.sleep", side_effect=self._bounded_sleep()),
        ):
            with pytest.raises(click.ClickException):
                _wait_for_dags("http://srv", "tok", ["dag-1"])

    def test_allow_unschedulable_wait_skips_and_succeeds(self):
        from cvcpkg.cli._builds import _wait_for_dags

        client = self._mock_client({1: "succeeded", 2: "unschedulable"})
        with (
            mock.patch("httpx.Client", return_value=client),
            mock.patch("cvcpkg.cli._builds.time.sleep", side_effect=self._bounded_sleep()),
        ):
            # --allow-unschedulable semantics: reaped jobs are skipped,
            # the wait returns cleanly.
            _wait_for_dags("http://srv", "tok", ["dag-1"], fail_on_unschedulable=False)

    def test_terminal_statuses_cover_reaped_states(self):
        from cvcpkg.cli._builds import _TERMINAL_STATUSES

        assert "unschedulable" in _TERMINAL_STATUSES
        assert "cancelled" in _TERMINAL_STATUSES
        assert "timed_out" in _TERMINAL_STATUSES


def test_python_dash_m_exits_nonzero_on_error():
    """``python -m cvcpkg`` must propagate main()'s return code — a
    silent exit 0 on errors breaks scripted bootstraps (set -e)."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "cvcpkg", "definitely-not-a-command"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0


# ── follow-dag / _wait_for_dags unbounded-wait guards (regression) ──


class TestFollowDagTermination:
    """follow-dag must always terminate.

    Regression for the populate-server runner burns (run 29630731633 sat
    78 min in "Follow build output" and was cancelled; 29277276176 hit the
    2 h job timeout).  All 12 jobs of 29630731633 had printed a terminal
    status, so the poll loop had already broken — the process was wedged
    afterwards in ThreadPoolExecutor.__exit__, joining a log-stream thread
    whose client was built with timeout=None and so blocked forever on a
    connection an intermediary had dropped.
    """

    def _stream(self, lines=(), block_on=None):
        """A fake SSE stream.  With block_on set, iter_lines() hangs until
        that event fires — standing in for a dropped connection."""

        class FakeStream:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def iter_lines(self):
                yield from lines
                if block_on is not None:
                    block_on.wait()

        return FakeStream()

    def _client(self, jobs_by_poll, stream):
        """Client serving a /v1/builds listing that advances per poll."""
        import itertools

        polls = itertools.count()

        class FakeClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, url, **kw):
                resp = mock.MagicMock()
                resp.status_code = 200
                if url.endswith("/v1/builds"):
                    n = min(next(polls), len(jobs_by_poll) - 1)
                    resp.json.return_value = {"jobs": jobs_by_poll[n]}
                else:  # builder lookup
                    resp.json.return_value = {"name": "b1"}
                return resp

            def stream(self, method, url, **kw):
                return stream

        return FakeClient

    def _job(self, status, jid=1):
        return {
            "id": jid,
            "recipe_name": "zlib",
            "platform": "linux",
            "arch": "x86_64",
            "status": status,
            "builder_id": None,
        }

    def _run_bounded(self, argv, patches, timeout=30):
        """Invoke the CLI under a watchdog thread.

        Every failure mode guarded here is "the command never returns", so
        the tests must fail on a regression rather than wedge the suite —
        which is the very pathology under test.
        """
        import contextlib
        import threading

        result = {}

        def _run():
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                result["ret"] = main(argv)

        runner = threading.Thread(target=_run, daemon=True)
        runner.start()
        runner.join(timeout=timeout)
        assert not runner.is_alive(), f"follow-dag did not return within {timeout}s"
        return result["ret"]

    def test_returns_when_a_log_stream_never_ends(self, capsys):
        """The exact 29630731633 shape: every job terminal, one stream stuck.

        Before the fix the summary line never printed because the executor
        shutdown joined the wedged follower.  Followers are now daemon
        threads joined with a grace period, so the command completes.
        """
        import threading

        stuck = threading.Event()
        client = self._client(
            [[self._job("running")], [self._job("succeeded")]],
            self._stream(lines=["data: building..."], block_on=stuck),
        )
        try:
            ret = self._run_bounded(
                ["builds", "follow-dag", "d", "--server", "https://s.x", "--token", "t"],
                [
                    mock.patch("httpx.Client", client),
                    mock.patch("cvcpkg.cli._builds.time.sleep"),
                ],
            )
            assert ret == 0
            assert "1/1 succeeded" in capsys.readouterr().out
        finally:
            stuck.set()  # release the parked daemon thread

    def test_empty_job_set_exits_cleanly(self, capsys):
        """A DAG that matches no jobs is a no-op, not an infinite wait."""
        client = self._client([[]], self._stream())
        ret = self._run_bounded(
            ["builds", "follow-dag", "d", "--server", "https://s.x", "--token", "t"],
            [
                mock.patch("httpx.Client", client),
                mock.patch("cvcpkg.cli._builds.time.sleep"),
            ],
        )
        assert ret == 0
        assert "nothing to follow" in capsys.readouterr().out

    def test_wait_timeout_exits_2_while_still_building(self, capsys):
        """--wait-timeout distinguishes "still building" from "job failed"."""
        client = self._client([[self._job("running")]], self._stream())
        clock = iter([0.0, 0.0, 10_000.0])

        def _monotonic():
            try:
                return next(clock)
            except StopIteration:
                return 10_000.0

        from cvcpkg.cli._builds import WAIT_TIMEOUT_EXIT_CODE

        ret = self._run_bounded(
            [
                "builds",
                "follow-dag",
                "d",
                "--server",
                "https://s.x",
                "--token",
                "t",
                "--wait-timeout",
                "60",
            ],
            [
                mock.patch("httpx.Client", client),
                mock.patch("cvcpkg.cli._builds.time.sleep"),
                mock.patch("cvcpkg.cli._builds.time.monotonic", _monotonic),
            ],
        )
        assert ret == WAIT_TIMEOUT_EXIT_CODE
        assert "still building" in capsys.readouterr().err

    def test_failure_outranks_timeout(self, capsys):
        """One job failed, another still building when the timeout fires:
        exit 1 (actionable failure) rather than 2."""
        jobs = [self._job("failed", jid=1), self._job("running", jid=2)]
        client = self._client([jobs], self._stream())
        clock = iter([0.0, 0.0, 10_000.0])

        def _monotonic():
            try:
                return next(clock)
            except StopIteration:
                return 10_000.0

        ret = self._run_bounded(
            [
                "builds",
                "follow-dag",
                "d",
                "--server",
                "https://s.x",
                "--token",
                "t",
                "--wait-timeout",
                "60",
            ],
            [
                mock.patch("httpx.Client", client),
                mock.patch("cvcpkg.cli._builds.time.sleep"),
                mock.patch("cvcpkg.cli._builds.time.monotonic", _monotonic),
            ],
        )
        assert ret == 1


class TestWaitForDagsBounds:
    """_wait_for_dags shares follow-dag's unbounded-wait shape."""

    def _client(self, jobs):
        def _get(url, headers=None, params=None):
            resp = mock.MagicMock()
            resp.status_code = 200
            if url.endswith("/v1/builds"):
                resp.json.return_value = {"jobs": jobs}
            else:
                jid = int(url.rsplit("/", 1)[1])
                resp.json.return_value = {"id": jid, "status": "running"}
            return resp

        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(return_value=client)
        client.__exit__ = mock.MagicMock(return_value=False)
        client.get.side_effect = _get
        return client

    def _bounded_sleep(self, limit=200):
        calls = {"n": 0}

        def _sleep(_seconds):
            calls["n"] += 1
            if calls["n"] > limit:
                raise AssertionError("wait loop did not terminate")

        return _sleep

    def test_empty_job_set_returns(self, capsys):
        """--skip-existing can drop every recipe, leaving nothing to wait on."""
        from cvcpkg.cli._builds import _wait_for_dags

        with (
            mock.patch("httpx.Client", return_value=self._client([])),
            mock.patch("cvcpkg.cli._builds.time.sleep", side_effect=self._bounded_sleep()),
        ):
            _wait_for_dags("http://srv", "tok", ["dag-1"])
        assert "nothing to wait for" in capsys.readouterr().out

    def test_wait_timeout_raises_exit_2(self):
        from cvcpkg.cli._builds import WAIT_TIMEOUT_EXIT_CODE, _wait_for_dags

        jobs = [{"id": 1, "status": "running", "recipe_name": "r", "platform": "p", "arch": "x"}]
        clock = iter([0.0, 10_000.0])

        def _monotonic():
            try:
                return next(clock)
            except StopIteration:
                return 10_000.0

        with (
            mock.patch("httpx.Client", return_value=self._client(jobs)),
            mock.patch("cvcpkg.cli._builds.time.sleep", side_effect=self._bounded_sleep()),
            mock.patch("cvcpkg.cli._builds.time.monotonic", _monotonic),
        ):
            with pytest.raises(SystemExit) as exc:
                _wait_for_dags("http://srv", "tok", ["dag-1"], wait_timeout=60.0)
        assert exc.value.code == WAIT_TIMEOUT_EXIT_CODE

    def test_server_errors_do_not_count_as_an_empty_dag(self):
        """A persistently failing server must not be mistaken for "no jobs".

        Otherwise the empty-set guard turns an unreachable server or an
        expired token into a clean exit 0, reporting a run that built
        nothing as green.
        """
        from cvcpkg.cli._builds import WAIT_TIMEOUT_EXIT_CODE, _wait_for_dags

        def _get(url, headers=None, params=None):
            resp = mock.MagicMock()
            resp.status_code = 500
            return resp

        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(return_value=client)
        client.__exit__ = mock.MagicMock(return_value=False)
        client.get.side_effect = _get

        clock = iter([0.0] + [0.0] * 60)

        def _monotonic():
            try:
                return next(clock)
            except StopIteration:
                return 10_000.0  # eventually trips --wait-timeout

        with (
            mock.patch("httpx.Client", return_value=client),
            mock.patch("cvcpkg.cli._builds.time.sleep", side_effect=self._bounded_sleep()),
            mock.patch("cvcpkg.cli._builds.time.monotonic", _monotonic),
        ):
            with pytest.raises(SystemExit) as exc:
                _wait_for_dags("http://srv", "tok", ["dag-1"], wait_timeout=60.0)
        # Timed out rather than falsely reporting "nothing to wait for".
        assert exc.value.code == WAIT_TIMEOUT_EXIT_CODE


class TestWaitForJobsBounds:
    """_wait_for_jobs shares the same unbounded-poll shape as the DAG
    waiters: a job that never reaches a terminal state pinned the runner
    until the workflow timeout."""

    def test_wait_timeout_raises_exit_2(self):
        from cvcpkg.cli._builds import WAIT_TIMEOUT_EXIT_CODE, _wait_for_jobs

        def _get(url, headers=None, params=None):
            resp = mock.MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "id": 7,
                "status": "running",  # never terminal
                "recipe_name": "zlib",
                "platform": "linux",
            }
            return resp

        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(return_value=client)
        client.__exit__ = mock.MagicMock(return_value=False)
        client.get.side_effect = _get

        calls = {"n": 0}

        def _sleep(_s):
            calls["n"] += 1
            if calls["n"] > 200:
                raise AssertionError("wait loop did not terminate")

        clock = iter([0.0, 10_000.0])

        def _monotonic():
            try:
                return next(clock)
            except StopIteration:
                return 10_000.0

        with (
            mock.patch("httpx.Client", return_value=client),
            mock.patch("cvcpkg.cli._builds.time.sleep", side_effect=_sleep),
            mock.patch("cvcpkg.cli._builds.time.monotonic", _monotonic),
        ):
            with pytest.raises(SystemExit) as exc:
                _wait_for_jobs("http://srv", "tok", [7], wait_timeout=60.0)
        assert exc.value.code == WAIT_TIMEOUT_EXIT_CODE

    def test_no_timeout_preserves_existing_behaviour(self):
        """Default (None) still waits for terminal states, as before."""
        from cvcpkg.cli._builds import _wait_for_jobs

        def _get(url, headers=None, params=None):
            resp = mock.MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "id": 7,
                "status": "succeeded",
                "recipe_name": "zlib",
                "platform": "linux",
            }
            return resp

        client = mock.MagicMock()
        client.__enter__ = mock.MagicMock(return_value=client)
        client.__exit__ = mock.MagicMock(return_value=False)
        client.get.side_effect = _get

        with (
            mock.patch("httpx.Client", return_value=client),
            mock.patch("cvcpkg.cli._builds.time.sleep"),
        ):
            _wait_for_jobs("http://srv", "tok", [7])
