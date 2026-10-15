# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Windows bundles as cvcpkg.org actually serves them: a zip under a .tar.zst name.

``cvcpkg pack`` writes a ``.zip`` on Windows (``create_archive``: windows ->
zip), but the server stores every published archive as
``<name>-<version>-<platform>-<arch>-<config>-<link>.tar.zst`` whatever its
content, and the client caches it under that URL basename.  Install has always
sniffed magic bytes, so these bundles install.  Uninstall, verify and repair
re-read the cached archive for its member list and picked the reader from the
filename suffix, so they handed zip bytes to ``tarfile`` and died -- which is
why libcvc's SDK bundling could not copy a prefix and uninstall the unused
Windows bundles from it.

The bundles here are built with the real builder functions
(``generate_manifest`` + ``stage_bundle`` + ``create_archive``), renamed to the
server's ``.tar.zst`` storage name, seeded into the content-addressed cache the
way a download leaves them, and installed with the real ``install_entry``.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from cvcpkg import cache as cache_mod
from cvcpkg import installer
from cvcpkg._archive import archive_format, open_tar
from cvcpkg.builder import Recipe, create_archive, generate_manifest, stage_bundle
from cvcpkg.cli import main
from cvcpkg.cli._publish import _extract_manifest
from cvcpkg.errors import InstallError
from cvcpkg.installer import extract_bundle
from cvcpkg.lockfile import LockEntry, Lockfile
from cvcpkg.manifest import CatalogEntry
from cvcpkg.uninstaller import read_archive

VERSION = "1.0.0+cvc.1"


def _write_recipe(recipe_dir: Path, name: str, deps: tuple[str, ...]) -> None:
    recipe_dir.mkdir(parents=True, exist_ok=True)
    recipe: dict = {
        "schema_version": 1,
        "recipe": {"name": name, "upstream_version": "1.0.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": f"third-party/{name}"},
        "build": {"matrix": [{"platform": "windows", "script": "build.ps1"}]},
        "package": {"files": ["bin/*", "lib/*", "include/*"], "cmake_packages": []},
    }
    if deps:
        recipe["depends"] = {"runtime": list(deps)}
    (recipe_dir / "recipe.yaml").write_text(yaml.safe_dump(recipe))
    (recipe_dir / "build.ps1").write_text("# build\n")


def _publish_windows_bundle(
    tmp_path: Path,
    cache_dir: Path,
    name: str,
    *,
    deps: tuple[str, ...] = (),
) -> tuple[LockEntry, CatalogEntry]:
    """Build a real Windows bundle and leave it where a download from
    cvcpkg.org would: zip bytes, ``.tar.zst`` name, in the sha256 cache.

    ``archive_url`` is a ``file://`` URI to the served copy so the paths that
    re-download (repair of a corrupt cache, uninstall of an evicted archive)
    resolve offline through the local storage backend.
    """
    recipe_dir = tmp_path / "recipes" / name
    _write_recipe(recipe_dir, name, deps)
    recipe = Recipe.load(recipe_dir)

    install_dir = tmp_path / f"install-{name}"
    for rel, content in {
        f"bin/{name}.dll": "MZ",
        f"lib/{name}.lib": "!<arch>",
        f"include/{name}/{name}.h": "#pragma once\n",
        # noarch-style python payload: install moves it to Lib/site-packages
        f"lib/python3.12/site-packages/{name}_mod.py": "value = 1\n",
    }.items():
        p = install_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    manifest = generate_manifest(recipe, install_dir, "windows", "x86_64", "release", "shared")
    staging = tmp_path / f"staging-{name}"
    staging.mkdir()
    stage_bundle(install_dir, manifest, staging, recipe_dir=recipe_dir)

    packed, sha256, size = create_archive(
        staging, tmp_path / "dist", name, VERSION, "windows", "x86_64", "release", "shared"
    )
    assert packed.suffix == ".zip"  # what `cvcpkg pack` writes on Windows

    # The server's storage name for that same upload.
    served = tmp_path / "served" / f"{name}-{VERSION}-windows-x86_64-release-shared.tar.zst"
    served.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(packed, served)
    assert served.read_bytes()[:4] == b"PK\x03\x04"

    archive_url = served.as_uri()
    # The download layer caches under the URL basename ('+' percent-encoded).
    cache_mod.store(cache_dir, sha256, archive_url.rsplit("/", 1)[-1], served.read_bytes())

    lock_entry = LockEntry(
        name=name,
        version=VERSION,
        upstream_version="1.0.0",
        sha256=sha256,
        size_bytes=size,
        archive_url=archive_url,
    )
    cat_entry = CatalogEntry(
        name=name,
        version=VERSION,
        upstream_version="1.0.0",
        cvc_revision=1,
        platform="windows",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256=sha256,
        size_bytes=size,
        archive_url=archive_url,
        source_release="",
    )
    return lock_entry, cat_entry


def _install(prefix: Path, cache_dir: Path, bundles: list[tuple[LockEntry, CatalogEntry]]) -> None:
    for _lock_entry, cat_entry in bundles:
        installer.install_entry(cat_entry, prefix, cache_dir, target_platform="windows")
    Lockfile(
        platform="windows",
        arch="x86_64",
        config="release",
        link="shared",
        bundles=[e for e, _ in bundles],
    ).write(prefix / "share" / "libcvc-deps" / "lockfile.yaml")


def _locked_names(prefix: Path) -> list[str]:
    lock = Lockfile.read(prefix / "share" / "libcvc-deps" / "lockfile.yaml")
    return [b.name for b in lock.bundles]


def _payload(prefix: Path, name: str) -> list[Path]:
    """Every payload path *name*'s bundle materialized in the prefix."""
    return [
        prefix / "bin" / f"{name}.dll",
        prefix / "lib" / f"{name}.lib",
        prefix / "include" / name / f"{name}.h",
        prefix / "Lib" / "site-packages" / f"{name}_mod.py",
    ]


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    d.mkdir()
    monkeypatch.setenv("CVCPKG_CACHE", str(d))
    return d


# ── The regression: uninstall of a served Windows bundle ────────


class TestUninstallWindowsBundle:
    def test_uninstall_removes_a_zip_cached_under_a_tar_zst_name(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        alpha = _publish_windows_bundle(tmp_path, cache_dir, "alpha")
        bravo = _publish_windows_bundle(tmp_path, cache_dir, "bravo")
        _install(prefix, cache_dir, [alpha, bravo])
        assert all(p.is_file() for p in _payload(prefix, "alpha") + _payload(prefix, "bravo"))

        ret = main(["uninstall", "alpha", "--prefix", str(prefix)])

        assert ret == 0, capsys.readouterr().err
        assert not any(p.exists() for p in _payload(prefix, "alpha"))
        assert not (prefix / "include" / "alpha").exists()  # emptied dir pruned
        assert all(p.is_file() for p in _payload(prefix, "bravo"))
        assert _locked_names(prefix) == ["bravo"]

    def test_uninstalling_the_last_bundle_takes_its_metadata_too(self, tmp_path, cache_dir):
        prefix = tmp_path / "prefix"
        _install(prefix, cache_dir, [_publish_windows_bundle(tmp_path, cache_dir, "alpha")])
        assert (prefix / "share" / "libcvc-deps" / "alpha" / "recipe" / "build.ps1").is_file()

        assert main(["uninstall", "alpha", "--prefix", str(prefix)]) == 0

        assert not any(p.exists() for p in _payload(prefix, "alpha"))
        assert not (prefix / "share" / "libcvc-deps" / "alpha").exists()
        assert _locked_names(prefix) == []

    def test_dependents_come_from_the_zip_manifest(self, tmp_path, cache_dir, capsys):
        """The dependency graph is read out of each zip's embedded manifest,
        so uninstall still refuses to strand a dependent, and --cascade
        still takes it along."""
        prefix = tmp_path / "prefix"
        base = _publish_windows_bundle(tmp_path, cache_dir, "base")
        app = _publish_windows_bundle(tmp_path, cache_dir, "app", deps=("base",))
        _install(prefix, cache_dir, [base, app])

        assert main(["uninstall", "base", "--prefix", str(prefix)]) != 0
        err = capsys.readouterr().err
        assert "app" in err and "--cascade" in err
        assert all(p.is_file() for p in _payload(prefix, "base"))

        assert main(["uninstall", "base", "--cascade", "--prefix", str(prefix)]) == 0
        assert not any(p.exists() for p in _payload(prefix, "base") + _payload(prefix, "app"))
        assert _locked_names(prefix) == []

    def test_evicted_archive_is_refetched_and_read(self, tmp_path, cache_dir, capsys):
        """A removal target whose archive left the cache is downloaded again
        under the same .tar.zst name -- and must still be readable."""
        prefix = tmp_path / "prefix"
        alpha = _publish_windows_bundle(tmp_path, cache_dir, "alpha")
        _install(prefix, cache_dir, [alpha])
        shutil.rmtree(cache_dir / alpha[0].sha256)

        assert main(["uninstall", "alpha", "--prefix", str(prefix)]) == 0

        assert "fetching archive" in capsys.readouterr().out
        assert not any(p.exists() for p in _payload(prefix, "alpha"))
        assert next((cache_dir / alpha[0].sha256).iterdir()).name.endswith(".tar.zst")


class TestVerifyAndRepairWindowsBundle:
    """verify and repair read the same cached archives through the same
    reader, so they were broken on every Windows prefix too."""

    def test_verify_passes_then_flags_a_missing_payload(self, tmp_path, cache_dir, capsys):
        prefix = tmp_path / "prefix"
        _install(prefix, cache_dir, [_publish_windows_bundle(tmp_path, cache_dir, "alpha")])

        assert main(["verify", "--prefix", str(prefix)]) == 0

        (prefix / "bin" / "alpha.dll").unlink()
        assert main(["verify", "--prefix", str(prefix)]) == 1
        assert "BROKEN" in capsys.readouterr().out

    def test_repair_restores_a_missing_payload(self, tmp_path, cache_dir):
        prefix = tmp_path / "prefix"
        _install(prefix, cache_dir, [_publish_windows_bundle(tmp_path, cache_dir, "alpha")])
        relocated = prefix / "Lib" / "site-packages" / "alpha_mod.py"
        relocated.unlink()

        assert main(["repair", "--prefix", str(prefix)]) == 0

        assert relocated.is_file()
        assert main(["verify", "--prefix", str(prefix)]) == 0


class TestPublishReadsAServedWindowsBundle:
    def test_extract_manifest_from_a_zip_named_tar_zst(self, tmp_path, cache_dir):
        """``cvcpkg publish`` of an archive fetched back from the server (to
        promote it elsewhere, say) reads its manifest by content, not name."""
        _publish_windows_bundle(tmp_path, cache_dir, "alpha")
        served = tmp_path / "served" / f"alpha-{VERSION}-windows-x86_64-release-shared.tar.zst"

        manifest = _extract_manifest(served)

        assert manifest["bundle"]["name"] == "alpha"
        assert manifest["bundle"]["platform"] == "windows"


# ── archive_format / open_tar ───────────────────────────────────


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


_MEMBERS = {"bin/tool": b"#!", "share/libcvc-deps/tool/manifest.yaml": b"bundle: {name: tool}\n"}
_TAR = _tar_bytes(_MEMBERS)
_TAR_GZ = gzip.compress(_TAR, mtime=0)
_ZIP = _zip_bytes(_MEMBERS)
_ZSTD_HEAD = b"\x28\xb5\x2f\xfd" + b"\0" * 32
_7Z_HEAD = b"7z\xbc\xaf\x27\x1c" + b"\0" * 32


class TestArchiveFormat:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param(_ZIP, "zip", id="zip"),
            pytest.param(_zip_bytes({}), "zip", id="empty-zip"),  # end-of-directory only
            pytest.param(_TAR_GZ, "tar.gz", id="gzip"),
            pytest.param(bz2.compress(_TAR), "tar.bz2", id="bzip2"),
            pytest.param(lzma.compress(_TAR), "tar.xz", id="xz"),
            pytest.param(_ZSTD_HEAD, "tar.zst", id="zstd"),
            pytest.param(_7Z_HEAD, "7z", id="7z"),
            pytest.param(_TAR, "tar", id="ustar"),  # signature at offset 257
        ],
    )
    @pytest.mark.parametrize("filename", ["bundle.tar.zst", "bundle.zip", "bundle"])
    def test_magic_bytes_beat_the_filename(self, tmp_path, content, expected, filename):
        path = tmp_path / filename
        path.write_bytes(content)
        assert archive_format(path) == expected

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("old.tar", "tar"),
            ("old.TGZ", "tar.gz"),
            ("old.tar.zstd", "tar.zst"),
            ("old.zip", "zip"),
        ],
    )
    def test_suffix_is_the_fallback_for_unsigned_content(self, tmp_path, filename, expected):
        path = tmp_path / filename
        path.write_bytes(b"\0" * 512)  # no signature anywhere
        assert archive_format(path) == expected

    def test_unknown_content_and_name_is_an_install_error(self, tmp_path):
        path = tmp_path / "bundle.rar"
        path.write_bytes(b"Rar!\x1a\x07\x00")
        with pytest.raises(InstallError, match="unsupported archive format"):
            archive_format(path)

    def test_open_tar_refuses_a_zip(self, tmp_path):
        path = tmp_path / "bundle.tar.zst"
        path.write_bytes(_ZIP)
        with pytest.raises(InstallError, match="zip archive, not a tar"):
            with open_tar(path):
                pass

    def test_zstd_without_tarfile_support_is_a_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tarfile.TarFile,
            "OPEN_METH",
            {k: v for k, v in tarfile.TarFile.OPEN_METH.items() if k != "zst"},
        )
        path = tmp_path / "bundle.tar.zst"
        path.write_bytes(_ZSTD_HEAD)
        with pytest.raises(InstallError, match="zstd-compressed tar.*Python 3.14"):
            with open_tar(path):
                pass

    @pytest.mark.skipif(
        "zst" not in tarfile.TarFile.OPEN_METH, reason="tarfile has no zstd (Python < 3.14)"
    )
    def test_zstd_tar_is_read_where_tarfile_supports_it(self, tmp_path):
        path = tmp_path / "tool-linux.tar.zst"
        with tarfile.open(path, "w:zst") as tf:
            for name, data in _MEMBERS.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

        assert read_archive(path, "tool") == (list(_MEMBERS), {"bundle": {"name": "tool"}})


class TestBundleReadersGoByContent:
    @pytest.mark.parametrize(
        ("content", "filename"),
        [
            # every Linux/macOS bundle on cvcpkg.org: a gzip tar under .tar.zst
            pytest.param(_TAR_GZ, "tool-linux.tar.zst", id="gzip-tar-named-tar.zst"),
            pytest.param(_ZIP, "tool-windows.tar.zst", id="zip-named-tar.zst"),
            pytest.param(_TAR_GZ, "tool.zip", id="gzip-tar-named-zip"),
        ],
    )
    def test_install_and_uninstall_readers_agree(self, tmp_path, content, filename):
        path = tmp_path / filename
        path.write_bytes(content)
        prefix = tmp_path / "prefix"

        extract_bundle(path, prefix)
        files, manifest = read_archive(path, "tool")

        assert files == list(_MEMBERS)
        assert all((prefix / f).is_file() for f in files)
        assert manifest == {"bundle": {"name": "tool"}}

    def test_read_archive_refuses_7z_by_content(self, tmp_path):
        path = tmp_path / "tool.tar.zst"
        path.write_bytes(_7Z_HEAD)
        with pytest.raises(InstallError, match=".7z archives are not supported"):
            read_archive(path, "tool")
