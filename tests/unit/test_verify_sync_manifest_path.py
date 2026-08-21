# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Regression coverage for the verify/sync manifest-path bug.

`cvcpkg verify` reported MISSING for every bundle on every prefix: it read
each bundle's manifest from `share/libcvc-deps/<name>/manifest.yaml`, but
`stage_bundle()` wrote it flat at `share/libcvc-deps/manifest.yaml` -- a path
every bundle shares, so a blind-merge install left only whichever bundle
extracted last.  `cvcpkg sync` read the same wrong path and, finding nothing,
re-downloaded and re-extracted the entire prefix on every run.

These tests build real bundle archives with the actual builder functions
(`generate_manifest` + `stage_bundle` + `create_archive`), extract them with
the actual installer function (`extract_bundle`), and drive `verify`/`sync`
through the CLI -- the full round trip, not a hand-authored fixture that
merely matches what the code happens to expect (which is exactly how this
bug went undetected: the pre-existing verify/sync unit tests built their own
manifest.yaml directly at the per-name path the code assumed, never
exercising what a real install actually produces).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import yaml

from cvcpkg.builder import Recipe, create_archive, generate_manifest, stage_bundle
from cvcpkg.cli import main
from cvcpkg.installer import extract_bundle
from cvcpkg.lockfile import LockEntry, Lockfile


def _write_recipe(recipe_dir: Path, name: str) -> Path:
    recipe_dir.mkdir(parents=True, exist_ok=True)
    p = recipe_dir / "recipe.yaml"
    p.write_text(
        yaml.dump(
            {
                "schema_version": 1,
                "recipe": {"name": name, "upstream_version": "1.0.0", "cvc_revision": 1},
                "source": {"type": "vendored", "path": f"third-party/{name}"},
                "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
                "package": {"files": ["lib/*"], "cmake_packages": []},
            },
            default_flow_style=False,
        )
    )
    return p


def _build_real_bundle(tmp_path: Path, dist_dir: Path, name: str) -> Path:
    """Build a real bundle archive the way `cvcpkg build-all` / `pack` do.

    Returns the archive path.  Exercises generate_manifest + stage_bundle +
    create_archive exactly as production code calls them, so a regression in
    where the manifest lands is caught here, not just in verify/sync's own
    read path.
    """
    recipe_dir = tmp_path / "recipes" / name
    _write_recipe(recipe_dir, name)
    recipe = Recipe.load(recipe_dir)

    install_dir = tmp_path / f"install-{name}"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / f"lib{name}.so").write_text("elf")

    manifest = generate_manifest(recipe, install_dir, "linux", "x86_64", "release", "shared")
    staging = tmp_path / f"staging-{name}"
    staging.mkdir()
    stage_bundle(install_dir, manifest, staging, recipe_dir=recipe_dir)

    archive, sha256, size = create_archive(
        staging, dist_dir, name, "1.0.0+cvc.1", "linux", "x86_64", "release", "shared"
    )
    return archive


class TestVerifyAfterRealInstall:
    def test_verify_passes_after_a_real_install(self, tmp_path):
        """install -> verify must round-trip: this is the reported bug."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        archive = _build_real_bundle(tmp_path, dist_dir, "zlib")

        prefix = tmp_path / "prefix"
        extract_bundle(archive, prefix)
        lock = Lockfile(
            platform="linux",
            arch="x86_64",
            config="release",
            link="shared",
            bundles=[LockEntry(name="zlib", version="1.0.0+cvc.1", upstream_version="1.0.0")],
        )
        lock.write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")

        ret = main(["verify", "--prefix", str(prefix)])

        assert ret == 0

    def test_verify_passes_for_every_bundle_in_a_multi_package_prefix(self, tmp_path):
        """The bug's defining symptom: extraction merges N bundles into one
        prefix, and a flat manifest.yaml only ever describes the last one --
        every earlier bundle reported MISSING regardless of being present."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        names = ["zlib", "libpng", "openssl"]
        prefix = tmp_path / "prefix"
        bundles = []
        for name in names:
            archive = _build_real_bundle(tmp_path, dist_dir, name)
            extract_bundle(archive, prefix)
            bundles.append(LockEntry(name=name, version="1.0.0+cvc.1", upstream_version="1.0.0"))

        Lockfile(
            platform="linux", arch="x86_64", config="release", link="shared", bundles=bundles
        ).write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")

        ret = main(["verify", "--prefix", str(prefix)])

        assert ret == 0
        for name in names:
            assert (prefix / "share" / "libcvc-deps" / name / "manifest.yaml").is_file()


class TestSyncAfterRealInstall:
    def test_sync_on_a_complete_prefix_downloads_nothing(self, tmp_path, capsys):
        """The bug's worse consequence: sync treated every bundle as missing
        and re-downloaded/re-extracted the whole prefix on every run."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        names = ["zlib", "libpng"]
        prefix = tmp_path / "prefix"
        bundles = []
        for name in names:
            archive = _build_real_bundle(tmp_path, dist_dir, name)
            extract_bundle(archive, prefix)
            bundles.append(
                LockEntry(
                    name=name,
                    version="1.0.0+cvc.1",
                    upstream_version="1.0.0",
                    archive_url=f"https://example.invalid/{archive.name}",
                )
            )

        Lockfile(
            platform="linux", arch="x86_64", config="release", link="shared", bundles=bundles
        ).write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")

        with mock.patch("cvcpkg.installer.install_entry") as m:
            ret = main(["sync", "--prefix", str(prefix)])

        assert ret == 0
        m.assert_not_called()
        assert "prefix is in sync" in capsys.readouterr().out
