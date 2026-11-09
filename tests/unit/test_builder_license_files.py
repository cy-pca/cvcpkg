"""Tests for collect_licenses: every bundle carries its upstream licence texts.

Most recipes never installed them. F2Dock's macOS .app bundles ~140 dylibs
from its cvcpkg closure, gmp and mpfr (LGPL-3.0) among them, and of the 45
components in that closure only a handful shipped any licence file, each in a
different place. collect_licenses copies them into share/licenses/<name>/ after
every build, so a bundle is redistributable as built.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import BuildError, Recipe, collect_licenses, license_dir


def _recipe(tmp_path: Path, name: str = "gmp", license_files: list[str] | None = None) -> Recipe:
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    package = "package:\n  files: [lib/]\n"
    if license_files:
        package += "  license_files:\n" + "".join(f"    - {g!r}\n" for g in license_files)
    (rdir / "recipe.yaml").write_text(
        "schema_version: 1\n"
        f"recipe:\n  name: {name}\n  upstream_version: '1.0'\n  cvc_revision: 1\n"
        "source:\n  type: vendored\n  path: .\n" + package
    )
    return Recipe.load(rdir)


def _tree(root: Path, *files: str) -> Path:
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"text of {rel}\n")
    return root


def test_top_level_licence_files_are_collected(tmp_path: Path) -> None:
    # gmp's real top level: the GPL and LGPL texts plus a COPYINGv2.
    src = _tree(tmp_path / "src", "COPYING", "COPYING.LESSERv3", "COPYINGv2", "README", "gmp-h.in")
    install = tmp_path / "install"
    written = collect_licenses(_recipe(tmp_path), src, install)

    dest = license_dir(install, "gmp")
    assert dest == install / "share" / "licenses" / "gmp"
    assert sorted(p.name for p in written) == ["COPYING", "COPYING.LESSERv3", "COPYINGv2"]
    assert (dest / "COPYING.LESSERv3").read_text() == "text of COPYING.LESSERv3\n"
    assert not (dest / "README").exists()


@pytest.mark.parametrize(
    "name",
    [
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.md",
        "LICENCE",
        "license",
        "LICENSE_1_0.txt",  # Boost
        "LICENSE-APACHE",
        "COPYRIGHT",
        "NOTICE",
        "NOTICE.txt",
        "UNLICENSE",
    ],
)
def test_licence_names_that_match(tmp_path: Path, name: str) -> None:
    src = _tree(tmp_path / "src", name)
    written = collect_licenses(_recipe(tmp_path), src, tmp_path / "install")
    assert [p.name for p in written] == [name]


@pytest.mark.parametrize("name", ["LICENSES.cmake", "COPYINGRULES", "README", "licensing.c"])
def test_names_that_do_not(tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]) -> None:
    # A licence word must be the whole name or be followed by . _ - or a
    # version (COPYINGv2): LICENSES.cmake and COPYINGRULES are something else.
    src = _tree(tmp_path / "src", name)
    assert collect_licenses(_recipe(tmp_path), src, tmp_path / "install") == []
    assert "WARNING" in capsys.readouterr().out


def test_a_reuse_licenses_directory_is_collected(tmp_path: Path) -> None:
    src = _tree(tmp_path / "src", "LICENSES/MIT.txt", "LICENSES/Apache-2.0.txt", "README")
    written = collect_licenses(_recipe(tmp_path), src, tmp_path / "install")
    assert sorted(p.name for p in written) == ["Apache-2.0.txt", "MIT.txt"]


def test_a_recipe_that_installs_its_own_is_left_alone(tmp_path: Path) -> None:
    # eigen, pegtl and others install share/licenses/<name>/ themselves.
    src = _tree(tmp_path / "src", "COPYING.MPL2", "COPYING.BSD")
    install = _tree(tmp_path / "install", "share/licenses/gmp/COPYING.MPL2")
    assert collect_licenses(_recipe(tmp_path), src, install) == []
    assert sorted(p.name for p in license_dir(install, "gmp").iterdir()) == ["COPYING.MPL2"]


def test_license_files_globs_replace_the_default(tmp_path: Path) -> None:
    src = _tree(tmp_path / "src", "LICENSE", "doc/COPYING.ftl", "doc/GPLv2.TXT")
    recipe = _recipe(tmp_path, "freetype", ["doc/*.TXT", "doc/COPYING*", "doc/GPLv2.TXT"])
    written = collect_licenses(recipe, src, tmp_path / "install")
    # The top-level LICENSE is not taken once globs are given, and a file two
    # globs both match is copied once.
    assert sorted(p.name for p in written) == ["COPYING.ftl", "GPLv2.TXT"]


def test_a_glob_that_matches_nothing_fails_the_build(tmp_path: Path) -> None:
    src = _tree(tmp_path / "src", "LICENSE")
    recipe = _recipe(tmp_path, "freetype", ["docs/LICENSE.TXT"])
    with pytest.raises(BuildError, match="docs/LICENSE.TXT"):
        collect_licenses(recipe, src, tmp_path / "install")


def test_same_name_from_two_directories_keeps_both(tmp_path: Path) -> None:
    src = _tree(tmp_path / "src", "zlib/LICENSE", "minizip/LICENSE")
    recipe = _recipe(tmp_path, "assimp", ["*/LICENSE"])
    written = collect_licenses(recipe, src, tmp_path / "install")
    assert sorted(p.name for p in written) == ["minizip_LICENSE", "zlib_LICENSE"]


def test_no_source_tree_collects_nothing(tmp_path: Path) -> None:
    install = tmp_path / "install"
    recipe = _recipe(tmp_path)
    assert collect_licenses(recipe, None, install) == []
    assert collect_licenses(recipe, tmp_path / "missing", install) == []
    assert not license_dir(install, "gmp").exists()


def test_recipe_load_reads_license_files(tmp_path: Path) -> None:
    assert _recipe(tmp_path, "a").license_files == []
    assert _recipe(tmp_path, "b", ["docs/LICENSE"]).license_files == ["docs/LICENSE"]


# ── pack --from-prefix ──────────────────────────────────────────
#
# libcvc and F2Dock publish with `cvcpkg pack --from-prefix`, from a tree
# their own CMake installed, so collect_licenses (which runs in build_recipe)
# never sees them. For a vendored recipe the texts go into the staged copy and
# the manifest, and the caller's prefix stays as it was.


def _from_prefix_repo(tmp_path: Path, *, prefix_has_own: bool = False) -> tuple[Path, Path]:
    """A repo with its recipe in cvcpkg/recipes/<name>/ (source ../../.. from
    cvcpkg/recipes/_vendored/, as libcvc's is) and a staged install tree."""
    repo = _tree(tmp_path / "repo", "LICENSE", "THIRD-PARTY.md", "src/a.c")
    rdir = repo / "cvcpkg" / "recipes" / "libfoo"
    rdir.mkdir(parents=True)
    (rdir / "recipe.yaml").write_text(
        "schema_version: 1\n"
        "recipe:\n  name: libfoo\n  upstream_version: '1.0.0'\n  cvc_revision: 1\n"
        "source:\n  type: vendored\n  path: ../../..\n"
        "build:\n  matrix:\n    - {platform: linux, script: build.sh}\n"
        "package:\n  files: [lib/*]\n"
    )
    (repo / "cvcpkg" / "recipes" / "_vendored").mkdir()
    stage = _tree(tmp_path / "stage", "lib/libfoo.so")
    if prefix_has_own:
        _tree(stage, "share/licenses/libfoo/COPYING")
    return rdir, stage


def _archive_members(archive: Path) -> tuple[set[str], dict]:
    import tarfile

    with tarfile.open(archive, "r:*") as tf:
        names = {m.name.removeprefix("./") for m in tf.getmembers()}
        mf = tf.extractfile("share/libcvc-deps/libfoo/manifest.yaml")
        assert mf is not None
        manifest = yaml.safe_load(mf.read())
    return names, manifest


def test_from_prefix_ships_a_vendored_recipes_licence(tmp_path: Path) -> None:
    from cvcpkg.builder import pack_from_prefix

    rdir, stage = _from_prefix_repo(tmp_path)
    before = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*"))
    archive, _, _ = pack_from_prefix(
        rdir, stage, platform="linux", arch="x86_64", output_dir=tmp_path / "dist"
    )
    names, manifest = _archive_members(archive)
    assert "share/licenses/libfoo/LICENSE" in names
    assert "share/licenses/libfoo/LICENSE" in manifest["contents"]["files"]
    # THIRD-PARTY.md is not a licence name; the caller's tree is untouched.
    assert "share/licenses/libfoo/THIRD-PARTY.md" not in names
    assert sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*")) == before


def test_from_prefix_keeps_a_prefix_that_has_its_own(tmp_path: Path) -> None:
    from cvcpkg.builder import pack_from_prefix

    rdir, stage = _from_prefix_repo(tmp_path, prefix_has_own=True)
    archive, _, _ = pack_from_prefix(
        rdir, stage, platform="linux", arch="x86_64", output_dir=tmp_path / "dist"
    )
    names, _ = _archive_members(archive)
    assert "share/licenses/libfoo/COPYING" in names
    assert "share/licenses/libfoo/LICENSE" not in names
