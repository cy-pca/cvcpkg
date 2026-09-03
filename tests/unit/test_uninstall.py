# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for ``cvcpkg uninstall`` (cvcpkg.cli._uninstall + cvcpkg.uninstaller).

The prefix keeps no per-package file record, so uninstall reads file lists
back out of the cached bundle archives the lockfile points at.  These tests
build real archives, place them in a fake cache (CVCPKG_CACHE), extract them
into a prefix through the real installer path, and drive the CLI.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from unittest import mock

import pytest
import yaml

from cvcpkg.cli import main
from cvcpkg.installer import extract_bundle
from cvcpkg.lockfile import LockEntry, Lockfile

# ── Fixture helpers ─────────────────────────────────────────────


def _manifest_dict(
    name: str,
    version: str,
    deps: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
) -> dict:
    return {
        "schema_version": 3,
        "bundle": {
            "name": name,
            "version": version,
            "upstream_version": version.split("+")[0],
            "cvc_revision": 1,
            "platform": "linux",
            "arch": "x86_64",
            "build_type": "release",
            "link": "shared",
        },
        "contents": {
            "description": "test bundle",
            "files": [],
            **({"provides": list(provides)} if provides else {}),
        },
        "dependencies": {"required": [{"name": d} for d in deps]},
        "integrity": {"sha256": "", "size_bytes": 0, "built_at": ""},
    }


def _make_bundle(
    cache_dir: Path,
    name: str,
    files: dict[str, str],
    version: str = "1.0.0+cvc.1",
    deps: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    legacy_flat_manifest: bool = False,
) -> tuple[LockEntry, Path]:
    """Build a bundle tar.gz, store it in the cache, return (entry, archive).

    The manifest is staged under share/libcvc-deps/<name>/manifest.yaml,
    matching real stage_bundle output.  legacy_flat_manifest builds an
    archive the OLD way (flat share/libcvc-deps/manifest.yaml) to exercise
    the fallback path for bundles published before that layout landed.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        mdata = yaml.safe_dump(_manifest_dict(name, version, deps, provides)).encode()
        manifest_path = (
            "share/libcvc-deps/manifest.yaml"
            if legacy_flat_manifest
            else f"share/libcvc-deps/{name}/manifest.yaml"
        )
        minfo = tarfile.TarInfo(manifest_path)
        minfo.size = len(mdata)
        tf.addfile(minfo, io.BytesIO(mdata))
    raw = buf.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    filename = f"libcvc-deps-{name}-{version}-linux-x86_64-release-shared.tar.gz"
    archive = cache_dir / sha / filename
    archive.parent.mkdir(parents=True)
    archive.write_bytes(raw)
    entry = LockEntry(
        name=name,
        version=version,
        upstream_version=version.split("+")[0],
        source_release="1.0.0",
        sha256=sha,
        size_bytes=len(raw),
        archive_url=f"https://example.invalid/download/{filename}",
    )
    return entry, archive


def _install_bundles(
    prefix: Path,
    bundles: list[tuple[LockEntry, Path]],
    platform: str = "linux",
) -> None:
    """Extract bundles into the prefix and write the lockfile, like install."""
    for _entry, archive in bundles:
        extract_bundle(archive, prefix)
    lock = Lockfile(
        platform=platform,
        arch="x86_64",
        config="release",
        link="shared",
        bundles=[e for e, _ in bundles],
    )
    lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    d.mkdir()
    monkeypatch.setenv("CVCPKG_CACHE", str(d))
    return d


def _read_lock(prefix: Path) -> Lockfile:
    return Lockfile.read(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


# ── CLI behaviour ───────────────────────────────────────────────


class TestUninstallCommand:
    def test_removes_files_prunes_dirs_updates_lockfile(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        alpha = _make_bundle(cache_dir, "alpha", {"bin/alpha": "#!", "lib/libalpha.so": "elf"})
        bravo = _make_bundle(cache_dir, "bravo", {"bin/bravo": "#!"})
        _install_bundles(prefix, [alpha, bravo])

        ret = main(["uninstall", "alpha", "--prefix", str(prefix)])

        assert ret == 0
        assert not (prefix / "bin" / "alpha").exists()
        assert not (prefix / "lib").exists()  # emptied and pruned
        assert (prefix / "bin" / "bravo").exists()
        # plan_removal treats anything under share/libcvc-deps/ as protected
        # while other packages remain installed, so alpha's own per-name
        # manifest is kept too -- conservative, since it is not actually
        # shared with bravo, but safe.
        assert (prefix / "share" / "libcvc-deps" / "alpha" / "manifest.yaml").exists()
        assert (prefix / "share" / "libcvc-deps" / "bravo" / "manifest.yaml").exists()
        assert [b.name for b in _read_lock(prefix).bundles] == ["bravo"]

    def test_unknown_package_errors(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        _install_bundles(prefix, [_make_bundle(cache_dir, "alpha", {"bin/alpha": "#!"})])

        ret = main(["uninstall", "nonesuch", "--prefix", str(prefix)])

        assert ret != 0
        err = capsys.readouterr().err
        assert "nonesuch" in err
        assert "alpha" in err  # tells the user what IS installed

    def test_no_lockfile_errors(self, tmp_path, cache_dir, capsys):
        ret = main(["uninstall", "alpha", "--prefix", str(tmp_path / "empty")])
        assert ret != 0
        assert "no lockfile" in capsys.readouterr().err.lower()

    def test_refuses_when_dependents_exist(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        alpha = _make_bundle(cache_dir, "alpha", {"lib/libalpha.so": "elf"})
        bravo = _make_bundle(cache_dir, "bravo", {"bin/bravo": "#!"}, deps=("alpha",))
        _install_bundles(prefix, [alpha, bravo])

        ret = main(["uninstall", "alpha", "--prefix", str(prefix)])

        assert ret != 0
        err = capsys.readouterr().err
        assert "bravo" in err
        assert "--cascade" in err
        # nothing was touched
        assert (prefix / "lib" / "libalpha.so").exists()
        assert [b.name for b in _read_lock(prefix).bundles] == ["alpha", "bravo"]

    def test_cascade_removes_dependent_closure(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        alpha = _make_bundle(cache_dir, "alpha", {"lib/libalpha.so": "elf"})
        bravo = _make_bundle(cache_dir, "bravo", {"lib/libbravo.so": "elf"}, deps=("alpha",))
        charlie = _make_bundle(cache_dir, "charlie", {"bin/charlie": "#!"}, deps=("bravo",))
        _install_bundles(prefix, [alpha, bravo, charlie])

        ret = main(["uninstall", "alpha", "--cascade", "--prefix", str(prefix)])

        assert ret == 0
        assert not (prefix / "lib").exists()
        assert not (prefix / "bin").exists()
        # last package gone: every per-name metadata slot goes with it
        assert not (prefix / "share" / "libcvc-deps" / "alpha").exists()
        assert not (prefix / "share" / "libcvc-deps" / "bravo").exists()
        assert not (prefix / "share" / "libcvc-deps" / "charlie").exists()
        assert _read_lock(prefix).bundles == []
        out = capsys.readouterr().out
        assert "[dependent]" in out

    def test_cascade_removes_dependents_never_dependencies(self, tmp_path, cache_dir, capsys):
        """--cascade follows the graph UP (things that would break), not down.
        A target's own dependencies stay: other things may use them, and
        "uninstall X" must never quietly strip X's substrate."""
        prefix = tmp_path / "prefix"
        base = _make_bundle(cache_dir, "base", {"lib/libbase.so": "elf"})
        mid = _make_bundle(cache_dir, "mid", {"lib/libmid.so": "elf"}, deps=("base",))
        top = _make_bundle(cache_dir, "top", {"bin/top": "#!"}, deps=("mid",))
        _install_bundles(prefix, [base, mid, top])

        ret = main(["uninstall", "mid", "--cascade", "--prefix", str(prefix)])

        assert ret == 0
        # mid and its dependent top are gone
        assert not (prefix / "lib" / "libmid.so").exists()
        assert not (prefix / "bin").exists()
        # mid's dependency base is untouched
        assert (prefix / "lib" / "libbase.so").exists()
        assert [b.name for b in _read_lock(prefix).bundles] == ["base"]

    def test_dependents_via_provides_slot(self, tmp_path, cache_dir, capsys):
        """A dep on a virtual name must reach the installed provider."""
        prefix = tmp_path / "prefix"
        py = _make_bundle(cache_dir, "python313x", {"bin/python3": "#!"}, provides=("pythonx",))
        app = _make_bundle(cache_dir, "appx", {"bin/appx": "#!"}, deps=("pythonx",))
        _install_bundles(prefix, [py, app])

        ret = main(["uninstall", "python313x", "--prefix", str(prefix)])

        assert ret != 0
        assert "appx" in capsys.readouterr().err

    def test_shared_payload_path_is_kept(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        shared = {"share/data/common.txt": "common"}
        alpha = _make_bundle(cache_dir, "alpha", {"bin/alpha": "#!", **shared})
        bravo = _make_bundle(cache_dir, "bravo", {"bin/bravo": "#!", **shared})
        _install_bundles(prefix, [alpha, bravo])

        ret = main(["uninstall", "alpha", "--prefix", str(prefix)])

        assert ret == 0
        assert not (prefix / "bin" / "alpha").exists()
        assert (prefix / "share" / "data" / "common.txt").exists()
        assert "keeping" in capsys.readouterr().out

    def test_source_built_entry_refused(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        alpha = _make_bundle(cache_dir, "alpha", {"bin/alpha": "#!"})
        source = LockEntry(
            name="handmade",
            version="source",
            source_release="source-build",
        )
        _install_bundles(prefix, [alpha])
        lock = _read_lock(prefix)
        lock.bundles.append(source)
        lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")

        ret = main(["uninstall", "handmade", "--prefix", str(prefix)])

        assert ret != 0
        assert "built from source" in capsys.readouterr().err.lower()

    def test_dry_run_changes_nothing(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        alpha = _make_bundle(cache_dir, "alpha", {"bin/alpha": "#!"})
        _install_bundles(prefix, [alpha])

        ret = main(["uninstall", "alpha", "--dry-run", "--prefix", str(prefix)])

        assert ret == 0
        assert (prefix / "bin" / "alpha").exists()
        assert [b.name for b in _read_lock(prefix).bundles] == ["alpha"]
        assert "dry run" in capsys.readouterr().out.lower()

    def test_evicted_archive_is_refetched(self, tmp_path, cache_dir, capsys):
        """A removal target's archive is re-downloaded when the cache lost it."""
        import shutil

        prefix = tmp_path / "prefix"
        alpha_entry, alpha_archive = _make_bundle(cache_dir, "alpha", {"bin/alpha": "#!"})
        _install_bundles(prefix, [(alpha_entry, alpha_archive)])
        stash = tmp_path / alpha_archive.name
        shutil.move(str(alpha_archive), str(stash))
        shutil.rmtree(alpha_archive.parent)

        def fake_download(entry, cdir, **kwargs):
            restored = cache_dir / entry.sha256 / stash.name
            restored.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(stash), str(restored))
            return restored

        with mock.patch("cvcpkg.installer.download_bundle", side_effect=fake_download):
            ret = main(["uninstall", "alpha", "--prefix", str(prefix)])

        assert ret == 0
        assert not (prefix / "bin" / "alpha").exists()
        assert "fetching archive" in capsys.readouterr().out

    def test_uncached_bystanders_warn_once_not_per_package(self, tmp_path, cache_dir, capsys):
        """A long-lived prefix can have many bundles aged out of the cache;
        their unknown deps are one summary line, not N lines."""
        prefix = tmp_path / "prefix"
        alpha = _make_bundle(cache_dir, "alpha", {"bin/alpha": "#!"})
        strangers = [
            LockEntry(
                name=f"stranger{i}",
                version="1.0.0+cvc.1",
                sha256="0" * 64,  # nothing at this hash in the cache
                archive_url=f"https://example.invalid/download/stranger{i}.tar.gz",
            )
            for i in range(3)
        ]
        _install_bundles(prefix, [alpha])
        lock = _read_lock(prefix)
        lock.bundles.extend(strangers)
        lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")

        ret = main(["uninstall", "alpha", "--prefix", str(prefix)])

        assert ret == 0
        err = capsys.readouterr().err
        assert err.count("cannot determine the dependencies") == 1
        for s in strangers:
            assert s.name in err

    def test_help_is_registered(self):
        try:
            ret = main(["uninstall", "--help"])
        except SystemExit as e:
            assert e.code == 0
        else:
            assert ret == 0


class TestConflictMessageAdvisesARealCommand:
    """The install-conflict error told users to run ``cvcpkg uninstall`` for
    months while no such command existed — copy-pasting it got "No such
    command".  Assert the advice against the registered command list, not
    against the message text, so the two cannot drift apart again."""

    def test_uninstall_command_is_registered(self):
        from cvcpkg.cli import cli

        assert "uninstall" in cli.commands

    def test_every_cvcpkg_command_the_message_suggests_exists(self, tmp_path):
        import re

        import click

        from cvcpkg.cli import cli
        from cvcpkg.cli._install import _check_conflicts

        rd = tmp_path / "recipes"
        for name, conflicts in [("python313", []), ("python313t", ["python313"])]:
            pkg_dir = rd / name
            pkg_dir.mkdir(parents=True)
            recipe: dict = {
                "schema_version": 1,
                "recipe": {"name": name, "upstream_version": "3.13.3", "cvc_revision": 1},
                "source": {"type": "vendored", "path": f"third-party/{name}"},
                "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
                "package": {"files": ["lib/*"], "cmake_packages": []},
            }
            if conflicts:
                recipe["conflicts"] = conflicts
            (pkg_dir / "recipe.yaml").write_text(yaml.safe_dump(recipe))
        prefix = tmp_path / "prefix"
        Lockfile(
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
            bundles=[LockEntry(name="python313", version="3.13.0+cvc.1")],
        ).write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")

        with pytest.raises(click.ClickException) as exc_info:
            _check_conflicts(["python313t"], prefix, [rd])
        msg = exc_info.value.format_message()

        suggested = set(re.findall(r"\bcvcpkg ([a-z][a-z0-9-]*)", msg))
        assert suggested, "message suggests no command at all"
        assert suggested <= set(cli.commands), (
            f"conflict message suggests non-existent command(s): "
            f"{sorted(suggested - set(cli.commands))}"
        )


# ── Unit tests for the pure helpers ─────────────────────────────


class TestUninstallerHelpers:
    def test_effective_path_windows_site_packages_remap(self):
        from cvcpkg.uninstaller import effective_path

        assert (
            effective_path("lib/python3.11/site-packages/numpy/__init__.py", "windows")
            == "Lib/site-packages/numpy/__init__.py"
        )
        assert (
            effective_path("lib/python3.11/site-packages/numpy/__init__.py", "linux")
            == "lib/python3.11/site-packages/numpy/__init__.py"
        )
        assert effective_path("bin/foo", "windows") == "bin/foo"

    def test_dependent_closure_transitive(self):
        from cvcpkg.uninstaller import InstalledPackage, dependent_closure

        def pkg(name, deps=(), provides=()):
            return InstalledPackage(
                entry=LockEntry(name=name, version="1"),
                deps=list(deps),
                provides=list(provides),
            )

        packages = {
            "a": pkg("a"),
            "b": pkg("b", deps=("a",)),
            "c": pkg("c", deps=("b",)),
            "d": pkg("d"),
        }
        assert dependent_closure({"a"}, packages) == {"a", "b", "c"}
        assert dependent_closure({"c"}, packages) == {"c"}
        assert dependent_closure({"d"}, packages) == {"d"}

    def test_dependent_closure_ignores_system_deps(self):
        from cvcpkg.uninstaller import InstalledPackage, dependent_closure

        packages = {
            "a": InstalledPackage(
                entry=LockEntry(name="a", version="1"), deps=["libc-not-installed"]
            ),
        }
        assert dependent_closure({"a"}, packages) == {"a"}

    def test_unsafe_archive_member_rejected(self, tmp_path):
        from cvcpkg.errors import InstallError
        from cvcpkg.uninstaller import read_archive

        evil = tmp_path / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tf:
            data = b"boom"
            info = tarfile.TarInfo("../outside")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with pytest.raises(InstallError):
            read_archive(evil, "evil")

    def test_read_archive_returns_files_and_manifest_in_one_open(self, tmp_path, cache_dir):
        """Files and manifest come from a single pass.  A second pass would
        re-decompress every bundle in the prefix (the manifest sorts last in
        the tar), which is painful for Qt6/VTK-sized archives."""
        from cvcpkg import uninstaller

        _entry, archive = _make_bundle(
            cache_dir, "alpha", {"bin/alpha": "#!", "lib/liba.so": "elf"}, deps=("zlib",)
        )
        opens = 0
        real_open = uninstaller.tarfile.open

        def counting_open(*args, **kwargs):
            nonlocal opens
            opens += 1
            return real_open(*args, **kwargs)

        with mock.patch.object(uninstaller.tarfile, "open", counting_open):
            files, manifest = uninstaller.read_archive(archive, "alpha")

        assert opens == 1
        assert "bin/alpha" in files
        assert "lib/liba.so" in files
        assert manifest is not None
        assert manifest["bundle"]["name"] == "alpha"

    def test_read_archive_tolerates_a_missing_manifest(self, tmp_path):
        from cvcpkg.uninstaller import read_archive

        plain = tmp_path / "plain.tar.gz"
        with tarfile.open(plain, "w:gz") as tf:
            data = b"x"
            info = tarfile.TarInfo("bin/thing")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        files, manifest = read_archive(plain, "thing")

        assert files == ["bin/thing"]
        assert manifest is None

    def test_read_archive_finds_legacy_flat_manifest(self, cache_dir):
        """Archives built before the per-name layout landed staged their
        manifest flat; uninstall must still read those already-published
        bundles, not just newly-built ones."""
        from cvcpkg.uninstaller import read_archive

        _entry, archive = _make_bundle(
            cache_dir, "alpha", {"bin/alpha": "#!"}, legacy_flat_manifest=True
        )

        files, manifest = read_archive(archive, "alpha")

        assert "share/libcvc-deps/manifest.yaml" in files
        assert manifest is not None
        assert manifest["bundle"]["name"] == "alpha"

    def test_execute_removal_counts_and_prunes(self, tmp_path):
        from cvcpkg.uninstaller import execute_removal

        prefix = tmp_path / "prefix"
        (prefix / "a" / "b").mkdir(parents=True)
        (prefix / "a" / "b" / "f1").write_text("x")
        (prefix / "keep").mkdir()
        (prefix / "keep" / "f2").write_text("y")

        result = execute_removal(prefix, ["a/b/f1", "a/b/gone"])

        assert result.removed == 1
        assert result.absent == 1
        assert result.dirs_pruned == 2  # a/b and a
        assert result.failed == []
        assert not (prefix / "a").exists()
        assert (prefix / "keep" / "f2").exists()

    def test_execute_removal_reports_undeletable_without_aborting(self, tmp_path):
        """A path replaced by a directory must not abort the whole removal:
        stopping midway would strand files removed with the lockfile unwritten."""
        from cvcpkg.uninstaller import execute_removal

        prefix = tmp_path / "prefix"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "now_a_dir").mkdir()
        (prefix / "bin" / "now_a_dir" / "child").write_text("x")
        (prefix / "bin" / "ordinary").write_text("y")

        result = execute_removal(prefix, ["bin/now_a_dir", "bin/ordinary"])

        assert result.removed == 1  # the ordinary file still went
        assert len(result.failed) == 1
        assert result.failed[0][0] == "bin/now_a_dir"
        assert not (prefix / "bin" / "ordinary").exists()
        assert (prefix / "bin" / "now_a_dir" / "child").exists()

    def test_execute_removal_never_escapes_the_prefix(self, tmp_path):
        """Pruning walks up only to the prefix, never above it."""
        from cvcpkg.uninstaller import execute_removal

        prefix = tmp_path / "prefix"
        (prefix / "solo").mkdir(parents=True)
        (prefix / "solo" / "f").write_text("x")
        sibling = tmp_path / "sibling"
        sibling.mkdir()

        result = execute_removal(prefix, ["solo/f"])

        assert result.dirs_pruned == 1
        assert prefix.is_dir()  # prefix itself survives even when emptied
        assert sibling.is_dir()
        assert tmp_path.is_dir()
