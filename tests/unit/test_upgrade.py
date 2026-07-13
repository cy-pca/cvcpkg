"""Tests for `cvcpkg upgrade`."""

from __future__ import annotations

from unittest import mock

import yaml

from cvcpkg.cli import main
from cvcpkg.lockfile import LockEntry, Lockfile


def _write_lock(prefix, version="1.3.1+cvc.1"):
    lock = Lockfile(
        platform="linux",
        arch="x86_64",
        config="release",
        link="shared",
        bundles=[
            LockEntry(
                name="zlib",
                version=version,
                upstream_version="1.3.1",
                archive_url="https://example.test/zlib-old.tar.gz",
                sha256="a" * 64,
                size_bytes=1,
            )
        ],
    )
    lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


def _write_catalog(path, version="1.3.1+cvc.2"):
    cat = {
        "revision": 5,
        "bundles": [
            {
                "name": "zlib",
                "version": version,
                "upstream_version": "1.3.1",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": "b" * 64,
                "size_bytes": 2,
                "archive_url": "https://example.test/zlib-new.tar.gz",
            }
        ],
    }
    path.write_text(yaml.safe_dump(cat))


def _read_lock(prefix):
    return Lockfile.read(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


def test_upgrade_updates_to_newer_version(tmp_path):
    prefix = tmp_path / "deps"
    _write_lock(prefix, "1.3.1+cvc.1")
    catfile = tmp_path / "catalog.yaml"
    _write_catalog(catfile, "1.3.1+cvc.2")

    import cvcpkg.installer as inst

    with mock.patch.object(inst, "install_entry", return_value=None) as m:
        ret = main(["upgrade", "--prefix", str(prefix), "--catalog", str(catfile)])

    assert ret == 0
    m.assert_called_once()
    # The catalog entry passed to install_entry is the new version.
    assert m.call_args.args[0].version == "1.3.1+cvc.2"
    # Lockfile now records the new version + its sha/url.
    lock = _read_lock(prefix)
    z = next(b for b in lock.bundles if b.name == "zlib")
    assert z.version == "1.3.1+cvc.2"
    assert z.archive_url == "https://example.test/zlib-new.tar.gz"


def test_upgrade_dry_run_changes_nothing(tmp_path):
    prefix = tmp_path / "deps"
    _write_lock(prefix, "1.3.1+cvc.1")
    catfile = tmp_path / "catalog.yaml"
    _write_catalog(catfile, "1.3.1+cvc.2")

    import cvcpkg.installer as inst

    with mock.patch.object(inst, "install_entry", return_value=None) as m:
        ret = main(["upgrade", "--prefix", str(prefix), "--catalog", str(catfile), "--dry-run"])

    assert ret == 0
    m.assert_not_called()
    assert _read_lock(prefix).bundles[0].version == "1.3.1+cvc.1"


def test_upgrade_noop_when_up_to_date(tmp_path, capsys):
    prefix = tmp_path / "deps"
    _write_lock(prefix, "1.3.1+cvc.2")
    catfile = tmp_path / "catalog.yaml"
    _write_catalog(catfile, "1.3.1+cvc.2")

    import cvcpkg.installer as inst

    with mock.patch.object(inst, "install_entry", return_value=None) as m:
        ret = main(["upgrade", "--prefix", str(prefix), "--catalog", str(catfile)])

    assert ret == 0
    m.assert_not_called()
    assert "up to date" in capsys.readouterr().out


def test_upgrade_no_lockfile_errors(tmp_path):
    assert main(["upgrade", "--prefix", str(tmp_path / "empty")]) == 1


def test_upgrade_unknown_component_errors(tmp_path):
    prefix = tmp_path / "deps"
    _write_lock(prefix, "1.3.1+cvc.1")
    catfile = tmp_path / "catalog.yaml"
    _write_catalog(catfile)
    ret = main(["upgrade", "nosuch", "--prefix", str(prefix), "--catalog", str(catfile)])
    assert ret == 1
