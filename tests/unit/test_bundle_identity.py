"""A bundle's identity (name / arch) must survive pack and publish.

Two ways it did not, both seen publishing libcvc's pycvc-gl-cp312 wasm bundle:

* ``cvcpkg pack --from-prefix`` of a prefix that also held the bundle's whole
  dependency closure archived every dependency's
  ``share/libcvc-deps/<dep>/manifest.yaml`` too, and ``cvcpkg publish`` took the
  first ``manifest.yaml`` in the (sorted) archive -- so the 242 MB pycvc-gl
  bundle went up as ``cvc/assimp==6.0.5+cvc.6``.  Publish now refuses an archive
  carrying several packages' manifests, and ``pack --from-prefix`` refuses such a
  prefix up front.
* plain ``cvcpkg pack --platform wasm`` (``pack_recipe``) tagged the bundle with
  the HOST arch (``...-wasm-x86_64-...``), unlike ``pack_from_prefix`` and install,
  which derive ``wasm32`` from the platform.  So did the arch the ``pack``,
  ``next-revision``, ``cascade-bump`` and ``publish`` commands looked bundles up by.
"""

from __future__ import annotations

import io
import tarfile
import types
import zipfile
from pathlib import Path
from unittest import mock

import click
import pytest
import yaml

from cvcpkg.builder import BuildContext, PackError, Recipe, pack_from_prefix, pack_recipe
from cvcpkg.cli import _build, _extract_manifest, main

RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "testpkg", "upstream_version": "1.0.0", "cvc_revision": 1},
    "source": {"type": "vendored", "path": "third-party/testpkg"},
    "patches": [],
    "build": {
        "matrix": [
            {"platform": "linux", "script": "build.sh"},
            {"platform": "wasm", "script": "build-wasm.sh"},
        ],
    },
    "package": {"files": ["lib/*"], "cmake_packages": []},
}


def _recipe_dir(tmp_path: Path) -> Path:
    d = tmp_path / "recipes" / "testpkg"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.dump(RECIPE))
    return d


def _manifest(name: str, **extra) -> bytes:
    return yaml.dump({"bundle": {"name": name, "version": "1.0.0+cvc.1", **extra}}).encode()


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    """A tar.gz with *members* in sorted order, as create_archive writes them."""
    with tarfile.open(path, "w:gz") as tf:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            tf.addfile(info, io.BytesIO(members[name]))
    return path


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in sorted(members):
            zf.writestr(name, members[name])
    return path


# ── publish: which manifest is the bundle's ─────────────────────


class TestExtractBundleManifest:
    def test_refuses_an_archive_carrying_its_dependencies_manifests(self, tmp_path):
        # The incident: assimp's manifest sorts first.
        archive = _tar(
            tmp_path / "pycvc-gl-cp312-3.5.1+cvc.1-wasm-wasm32-release-static.tar.gz",
            {
                "share/libcvc-deps/assimp/manifest.yaml": _manifest("assimp"),
                "share/libcvc-deps/boost/manifest.yaml": _manifest("boost"),
                "share/libcvc-deps/pycvc-gl-cp312/manifest.yaml": _manifest("pycvc-gl-cp312"),
                "lib/libassimp.a": b"x",
            },
        )
        with pytest.raises(click.ClickException) as exc:
            _extract_manifest(archive)
        msg = exc.value.format_message()
        assert "3 packages (assimp, boost, pycvc-gl-cp312)" in msg
        assert "--from-prefix" in msg

    def test_refuses_a_multi_package_zip(self, tmp_path):
        archive = _zip(
            tmp_path / "a.zip",
            {
                "share/libcvc-deps/a/manifest.yaml": _manifest("a"),
                "share/libcvc-deps/b/manifest.yaml": _manifest("b"),
            },
        )
        with pytest.raises(click.ClickException, match=r"2 packages \(a, b\)"):
            _extract_manifest(archive)

    def test_long_package_list_is_truncated(self, tmp_path):
        members = {
            f"share/libcvc-deps/p{i:02d}/manifest.yaml": _manifest(f"p{i:02d}") for i in range(12)
        }
        with pytest.raises(click.ClickException, match=r"p09, \.\.\. \(12 in all\)"):
            _extract_manifest(_tar(tmp_path / "x.tar.gz", members))

    def test_bundle_manifest_beats_a_stray_one_that_sorts_first(self, tmp_path):
        # A payload file that happens to be called manifest.yaml is not the bundle's.
        archive = _tar(
            tmp_path / "x.tar.gz",
            {
                "lib/python3.12/site-packages/aaa/manifest.yaml": b"just: data\n",
                "share/libcvc-deps/testpkg/manifest.yaml": _manifest("testpkg"),
            },
        )
        assert _extract_manifest(archive)["bundle"]["name"] == "testpkg"

    def test_same_package_flat_and_per_name_copies_prefer_per_name(self, tmp_path):
        archive = _tar(
            tmp_path / "x.tar.gz",
            {
                "share/libcvc-deps/manifest.yaml": _manifest("testpkg", arch="old"),
                "share/libcvc-deps/testpkg/manifest.yaml": _manifest("testpkg", arch="new"),
            },
        )
        assert _extract_manifest(archive)["bundle"]["arch"] == "new"

    def test_dot_slash_member_names(self, tmp_path):
        # `tar -C stage .` style archives.
        archive = _tar(
            tmp_path / "x.tar.gz",
            {"./share/libcvc-deps/testpkg/manifest.yaml": _manifest("testpkg")},
        )
        assert _extract_manifest(archive)["bundle"]["name"] == "testpkg"

    def test_root_manifest_still_read_when_there_is_no_bundle_one(self, tmp_path):
        archive = _tar(tmp_path / "x.tar.gz", {"manifest.yaml": _manifest("legacy")})
        assert _extract_manifest(archive)["bundle"]["name"] == "legacy"


# ── pack --from-prefix: refuse a prefix holding other packages ──


class TestPackFromPrefixRefusesOtherPackages:
    def _prefix(self, tmp_path: Path) -> Path:
        prefix = tmp_path / "prefix"
        (prefix / "lib").mkdir(parents=True)
        (prefix / "lib" / "libtestpkg.a").write_bytes(b"fake static lib")
        return prefix

    def _install_manifest(self, prefix: Path, rel: str, name: str) -> None:
        p = prefix / "share" / "libcvc-deps" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_manifest(name))

    def test_dependency_closure_prefix_is_refused(self, tmp_path):
        prefix = self._prefix(tmp_path)
        self._install_manifest(prefix, "assimp/manifest.yaml", "assimp")
        self._install_manifest(prefix, "boost/manifest.yaml", "boost")
        with pytest.raises(PackError, match=r"manifests of assimp, boost"):
            pack_from_prefix(
                _recipe_dir(tmp_path), prefix, platform="wasm", output_dir=tmp_path / "dist"
            )
        assert not (tmp_path / "dist").exists()

    def test_flat_manifest_of_another_package_is_refused(self, tmp_path):
        prefix = self._prefix(tmp_path)
        self._install_manifest(prefix, "manifest.yaml", "zlib")
        with pytest.raises(PackError, match=r"manifests of zlib"):
            pack_from_prefix(
                _recipe_dir(tmp_path), prefix, platform="linux", output_dir=tmp_path / "dist"
            )

    def test_unreadable_flat_manifest_is_refused_by_path(self, tmp_path):
        prefix = self._prefix(tmp_path)
        (prefix / "share" / "libcvc-deps").mkdir(parents=True)
        (prefix / "share" / "libcvc-deps" / "manifest.yaml").write_text("{not: [yaml")
        with pytest.raises(PackError, match=r"share/libcvc-deps/manifest\.yaml"):
            pack_from_prefix(
                _recipe_dir(tmp_path), prefix, platform="linux", output_dir=tmp_path / "dist"
            )

    @pytest.mark.parametrize("rel", ["testpkg/manifest.yaml", "manifest.yaml"])
    def test_own_installed_manifest_repacks(self, tmp_path, rel):
        # Re-packing an installed copy of the same package is fine, and what comes
        # out publishes as that package.
        prefix = self._prefix(tmp_path)
        self._install_manifest(prefix, rel, "testpkg")
        archive, _, _ = pack_from_prefix(
            _recipe_dir(tmp_path), prefix, platform="wasm", output_dir=tmp_path / "dist"
        )
        bundle = _extract_manifest(archive)["bundle"]
        assert (bundle["name"], bundle["arch"]) == ("testpkg", "wasm32")


# ── pack: the arch comes from the platform, not the host ────────


class TestPackRecipeArch:
    def _pack(self, tmp_path, monkeypatch, platform: str) -> Path:
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        recipe_dir = _recipe_dir(tmp_path)
        work = tmp_path / "work"
        install = work / "install"
        (install / "lib").mkdir(parents=True)
        (install / "lib" / "libtestpkg.a").write_bytes(b"fake static lib")
        built: dict = {}

        def fake_build_recipe(rd, **kw):
            built.update(kw)
            return BuildContext(
                recipe=Recipe.load(rd),
                platform=kw["platform"],
                config="release",
                link="static",
                prefix=install,
                source_dir=work / "src",
                build_dir=work / "build",
                install_dir=install,
                work_dir=work,
            )

        monkeypatch.setattr("cvcpkg.builder.build_recipe", fake_build_recipe)
        archive, _, _ = pack_recipe(recipe_dir, platform=platform, output_dir=tmp_path / "dist")
        assert built["platform"] == platform
        return archive

    def test_wasm_bundle_is_wasm32_on_an_x86_64_host(self, tmp_path, monkeypatch):
        archive = self._pack(tmp_path, monkeypatch, "wasm")
        assert archive.name == "testpkg-1.0.0+cvc.1-wasm-wasm32-release-static.tar.gz"
        bundle = _extract_manifest(archive)["bundle"]
        assert (bundle["platform"], bundle["arch"]) == ("wasm", "wasm32")

    def test_native_bundle_keeps_the_host_arch(self, tmp_path, monkeypatch):
        archive = self._pack(tmp_path, monkeypatch, "linux")
        assert "-linux-x86_64-" in archive.name


# ── CLI: bundles are looked up by the arch they were tagged with ─


def _fake_recipe(name: str = "testpkg") -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, cvc_revision=1, full_version="1.0.0+cvc.1")


class TestCliArchFollowsPlatform:
    @pytest.fixture(autouse=True)
    def _x86_64_host(self, monkeypatch):
        monkeypatch.setattr("cvcpkg.platform.detect_arch", lambda: "x86_64")
        monkeypatch.setattr("cvcpkg.config.default_server_url", lambda: "https://srv")

    def test_pack_bump_looks_up_the_wasm32_variant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda name, *a, **k: tmp_path / name)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: _fake_recipe())
        )
        resolve = mock.MagicMock(return_value=None)
        monkeypatch.setattr(_build, "_resolve_bump_revision", resolve)
        monkeypatch.setattr(
            "cvcpkg.builder.pack_recipe", mock.MagicMock(return_value=(tmp_path / "a", "s", 1))
        )
        argv = ["pack", "testpkg", "--local", "--platform", "wasm", "--output-dir", str(tmp_path)]
        assert main(argv) == 0
        assert resolve.call_args.kwargs["arch"] == "wasm32"

    def test_next_revision_asks_about_wasm32(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_build, "_resolve_recipe_dir", lambda *a, **k: tmp_path)
        monkeypatch.setattr(
            "cvcpkg.builder.Recipe", types.SimpleNamespace(load=lambda d: _fake_recipe())
        )
        compute = mock.MagicMock(return_value=4)
        monkeypatch.setattr("cvcpkg.revisions.compute_pack_revision", compute)
        assert main(["next-revision", "testpkg", "--platform", "wasm"]) == 0
        assert compute.call_args.kwargs["arch"] == "wasm32"
        assert capsys.readouterr().out.strip() == "4"

    def test_cascade_bump_asks_about_wasm32(self, tmp_path, monkeypatch):
        def fake_rev_bump(name, rdir, **kw):
            return [("testpkg", 1, kw["revision_for"](_fake_recipe()))]

        monkeypatch.setattr("cvcpkg.builder.rev_bump", fake_rev_bump)
        compute = mock.MagicMock(return_value=5)
        monkeypatch.setattr("cvcpkg.revisions.compute_pack_revision", compute)
        argv = ["cascade-bump", "testpkg", "--platform", "wasm", "--recipes-dir", str(tmp_path)]
        assert main([*argv, "--no-default-recipes"]) == 0
        assert compute.call_args.kwargs["arch"] == "wasm32"

    @pytest.mark.parametrize("by", ["--all", "testpkg"])
    def test_publish_finds_the_wasm32_archive(self, tmp_path, by):
        dist = tmp_path / "dist"
        _tar(
            (dist.mkdir() or dist) / "testpkg-1.0.0+cvc.1-wasm-wasm32-release-static.tar.gz",
            {"share/libcvc-deps/testpkg/manifest.yaml": _manifest("testpkg")},
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        argv = ["publish", by, "--platform", "wasm", "--link", "static", "--output-dir", str(dist)]
        assert main([*argv, "--dest", repo.as_uri()]) == 0
        assert (repo / "testpkg-1.0.0+cvc.1-wasm-wasm32-release-static.tar.gz").is_file()
