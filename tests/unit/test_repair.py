# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Coverage for ``cvcpkg repair`` and the strengthened ``cvcpkg verify``.

Like ``test_verify_sync_manifest_path``, these build real bundle archives with
the actual builder functions (``generate_manifest`` + ``stage_bundle`` +
``create_archive``), seed the content-addressed download cache exactly as a
real install leaves it, and install them into a prefix with the real
``install_entry`` -- so the member-list / relocation / removal logic is
exercised end to end, not against a hand-authored fixture.

``archive_url`` points at the on-disk archive via a ``file://`` URI so that the
one path that actually re-downloads (a corrupt cache) resolves offline through
the local storage backend.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml

from cvcpkg import cache as cache_mod
from cvcpkg import installer
from cvcpkg.builder import Recipe, create_archive, generate_manifest, stage_bundle
from cvcpkg.cli import main
from cvcpkg.lockfile import LockEntry, Lockfile
from cvcpkg.manifest import CatalogEntry


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


def _build_archive(
    tmp_path: Path,
    dist_dir: Path,
    name: str,
    *,
    platform: str = "linux",
    arch: str = "x86_64",
    lib_content: str = "elf",
    py_payload: bool = False,
    extra_files: dict[str, str] | None = None,
    variant: str = "",
) -> tuple[Path, str, int]:
    """Build one real bundle archive; return ``(archive, sha256, size)``."""
    recipe_dir = tmp_path / "recipes" / (name + variant)
    _write_recipe(recipe_dir, name)
    recipe = Recipe.load(recipe_dir)

    install_dir = tmp_path / f"install-{name}{variant}-{platform}"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / f"lib{name}.so").write_text(lib_content)
    if py_payload:
        site = install_dir / "lib" / "python3.12" / "site-packages"
        site.mkdir(parents=True)
        (site / f"{name}_mod.py").write_text("value = 1\n")
    for rel, content in (extra_files or {}).items():
        p = install_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    manifest = generate_manifest(recipe, install_dir, platform, arch, "release", "shared")
    staging = tmp_path / f"staging-{name}{variant}-{platform}"
    staging.mkdir()
    stage_bundle(install_dir, manifest, staging, recipe_dir=recipe_dir)

    return create_archive(
        staging, dist_dir, name, "1.0.0+cvc.1", platform, arch, "release", "shared"
    )


def _install_real_bundle(
    tmp_path: Path,
    dist_dir: Path,
    cache_dir: Path,
    prefix: Path,
    name: str,
    *,
    platform: str = "linux",
    arch: str = "x86_64",
    **kw,
) -> LockEntry:
    """Build a bundle, seed the download cache, and install it into *prefix*.

    Mirrors what ``cvcpkg install`` leaves behind: the archive sitting in the
    content-addressed cache and its tree merged into the prefix.  Returns the
    matching ``LockEntry`` (with ``archive_url`` set to the on-disk archive).
    """
    archive, sha256, size = _build_archive(
        tmp_path, dist_dir, name, platform=platform, arch=arch, **kw
    )
    archive_url = archive.as_uri()
    # The download layer derives the cache filename from the URL basename, which
    # is percent-encoded (the '+' in the version becomes %2B); seed under that
    # same name so is_cached / load_installed find it.
    cache_name = archive_url.rsplit("/", 1)[-1]
    cache_mod.store(cache_dir, sha256, cache_name, archive.read_bytes())

    entry = LockEntry(
        name=name,
        version="1.0.0+cvc.1",
        upstream_version="1.0.0",
        sha256=sha256,
        size_bytes=size,
        archive_url=archive_url,
    )
    cat = CatalogEntry(
        name=name,
        version="1.0.0+cvc.1",
        upstream_version="1.0.0",
        cvc_revision=1,
        platform=platform,
        arch=arch,
        build_type="release",
        link="shared",
        sha256=sha256,
        size_bytes=size,
        archive_url=archive_url,
        source_release="",
    )
    installer.install_entry(cat, prefix, cache_dir, target_platform=platform)
    return entry


def _write_lock(prefix: Path, bundles: list[LockEntry], platform: str = "linux") -> None:
    Lockfile(
        platform=platform,
        arch="x86_64",
        config="release",
        link="shared",
        bundles=bundles,
    ).write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Common per-test scaffolding: an isolated cache + dist dir + prefix."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("CVCPKG_CACHE", str(cache_dir))
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    prefix = tmp_path / "prefix"
    return {"cache": cache_dir, "dist": dist_dir, "prefix": prefix}


class TestVerifyAndRepair:
    def test_deleted_payload_is_flagged_then_repaired(self, tmp_path, env, capsys):
        """(a) A deleted payload lib: verify flags it, repair restores it, and a
        following verify passes again."""
        entry = _install_real_bundle(tmp_path, env["dist"], env["cache"], env["prefix"], "zlib")
        _write_lock(env["prefix"], [entry])

        lib = env["prefix"] / "lib" / "libzlib.so"
        assert lib.is_file()
        lib.unlink()

        assert main(["verify", "--prefix", str(env["prefix"])]) == 1
        out = capsys.readouterr()
        assert "BROKEN" in out.out
        assert "repair" in out.err  # the hint

        assert main(["repair", "--prefix", str(env["prefix"])]) == 0
        assert lib.is_file()  # restored

        assert main(["verify", "--prefix", str(env["prefix"])]) == 0

    def test_missing_manifest_is_repaired(self, tmp_path, env):
        """(b) A deleted manifest (level-1 breakage) is restored."""
        entry = _install_real_bundle(tmp_path, env["dist"], env["cache"], env["prefix"], "zlib")
        _write_lock(env["prefix"], [entry])

        manifest = env["prefix"] / "share" / "libcvc-deps" / "zlib" / "manifest.yaml"
        assert manifest.is_file()
        manifest.unlink()

        assert main(["repair", "--prefix", str(env["prefix"])]) == 0
        assert manifest.is_file()

    def test_corrupt_cached_archive_is_refetched(self, tmp_path, env):
        """(c) A cached archive whose bytes no longer hash to the lockfile sha is
        evicted and re-downloaded; install_entry runs."""
        entry = _install_real_bundle(tmp_path, env["dist"], env["cache"], env["prefix"], "zlib")
        _write_lock(env["prefix"], [entry])

        # Overwrite the cached archive with a different (but valid, same-paths)
        # archive so its bytes mismatch the lockfile sha256.
        dist2 = tmp_path / "dist2"
        dist2.mkdir()
        corrupt, _, _ = _build_archive(
            tmp_path, dist2, "zlib", lib_content="TAMPERED", variant="-bad"
        )
        cache_name = entry.archive_url.rsplit("/", 1)[-1]
        cache_mod.store(env["cache"], entry.sha256, cache_name, corrupt.read_bytes())

        real = installer.install_entry
        with mock.patch("cvcpkg.installer.install_entry", wraps=real) as m:
            ret = main(["repair", "--prefix", str(env["prefix"])])

        assert ret == 0
        m.assert_called()  # the corrupt bundle was re-fetched and reinstalled
        # Cache now holds the correct bytes again.
        assert cache_mod.file_sha256(env["cache"] / entry.sha256 / cache_name) == entry.sha256

    def test_dry_run_changes_nothing(self, tmp_path, env, capsys):
        """(d) --dry-run reports the broken set but touches nothing."""
        entry = _install_real_bundle(tmp_path, env["dist"], env["cache"], env["prefix"], "zlib")
        _write_lock(env["prefix"], [entry])

        lib = env["prefix"] / "lib" / "libzlib.so"
        lib.unlink()

        real = installer.install_entry
        with mock.patch("cvcpkg.installer.install_entry", wraps=real) as m:
            ret = main(["repair", "--prefix", str(env["prefix"]), "--dry-run"])

        assert ret == 0
        m.assert_not_called()
        assert not lib.exists()  # not restored
        out = capsys.readouterr().out
        assert "dry run" in out
        assert "zlib" in out

    def test_force_reinstalls_a_healthy_bundle(self, tmp_path, env):
        """(e) --force reinstalls a bundle that looks fine."""
        entry = _install_real_bundle(tmp_path, env["dist"], env["cache"], env["prefix"], "zlib")
        _write_lock(env["prefix"], [entry])

        real = installer.install_entry
        with mock.patch("cvcpkg.installer.install_entry", wraps=real) as m:
            ret = main(["repair", "--prefix", str(env["prefix"]), "--force"])

        assert ret == 0
        m.assert_called()
        assert (env["prefix"] / "lib" / "libzlib.so").is_file()

    def test_shared_file_survives_repair_of_a_sibling(self, tmp_path, env):
        """(f) Repairing one bundle keeps a path (and the metadata slot) a
        co-installed bundle also owns; only the broken bundle is reinstalled."""
        shared = {"lib/shared.txt": "common"}
        a = _install_real_bundle(
            tmp_path, env["dist"], env["cache"], env["prefix"], "aaa", extra_files=shared
        )
        b = _install_real_bundle(
            tmp_path, env["dist"], env["cache"], env["prefix"], "bbb", extra_files=shared
        )
        _write_lock(env["prefix"], [a, b])

        # Break only aaa's own payload.
        (env["prefix"] / "lib" / "libaaa.so").unlink()

        real = installer.install_entry
        with mock.patch("cvcpkg.installer.install_entry", wraps=real) as m:
            ret = main(["repair", "aaa", "--prefix", str(env["prefix"])])

        assert ret == 0
        # Only the broken bundle was reinstalled -- the survivor's files were
        # protected, not restored.
        assert m.call_count == 1
        assert (env["prefix"] / "lib" / "shared.txt").is_file()  # shared, kept
        assert (env["prefix"] / "lib" / "libbbb.so").is_file()  # survivor payload
        assert (
            env["prefix"] / "share" / "libcvc-deps" / "bbb" / "manifest.yaml"
        ).is_file()  # survivor metadata slot
        assert (env["prefix"] / "lib" / "libaaa.so").is_file()  # restored

    def test_source_built_bundle_is_unrepairable_not_a_crash(self, tmp_path, env, capsys):
        """(g) A source-built bundle (no archive_url) is reported, not a crash."""
        env["prefix"].mkdir()
        entry = LockEntry(name="fromsrc", version="source", archive_url="")
        _write_lock(env["prefix"], [entry])

        ret = main(["repair", "--prefix", str(env["prefix"])])
        assert ret == 0
        out = capsys.readouterr().out
        assert "SKIP" in out
        assert "nothing to repair" in out

    def test_unknown_component_errors(self, tmp_path, env):
        """(h) Naming a component that is not installed is a clean error."""
        entry = _install_real_bundle(tmp_path, env["dist"], env["cache"], env["prefix"], "zlib")
        _write_lock(env["prefix"], [entry])

        ret = main(["repair", "nope", "--prefix", str(env["prefix"])])
        assert ret == 1

    def test_windows_relocation_presence_and_removal(self, tmp_path, env):
        """(i) A windows bundle's python payload is relocated to Lib/site-packages;
        repair maps the member through effective_path for both presence and
        removal."""
        entry = _install_real_bundle(
            tmp_path,
            env["dist"],
            env["cache"],
            env["prefix"],
            "pyzlib",
            platform="windows",
            py_payload=True,
        )
        _write_lock(env["prefix"], [entry], platform="windows")

        relocated = env["prefix"] / "Lib" / "site-packages" / "pyzlib_mod.py"
        assert relocated.is_file()  # install relocated it
        # Strengthened verify sees the mapped path.
        assert main(["verify", "--prefix", str(env["prefix"])]) == 0

        relocated.unlink()
        assert main(["verify", "--prefix", str(env["prefix"])]) == 1

        assert main(["repair", "--prefix", str(env["prefix"])]) == 0
        assert relocated.is_file()  # restored to the relocated location
