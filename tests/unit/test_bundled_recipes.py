# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The frozen binary embeds the standard recipes as one zip in a generated
module (cvcpkg.bundled_recipes) instead of ~3,300 onefile data files, and
find_recipes_dir() unpacks them once per digest into the cache.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import os
import stat
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

from cvcpkg import bundled_recipes as br
from cvcpkg.builder import find_recipes_dir


def _tree(base):
    """A small recipes tree: _common/ plus one recipe with an executable script."""
    (base / "_common").mkdir(parents=True)
    (base / "_common" / "env.sh").write_text("# env\n")
    (base / "zlib").mkdir()
    (base / "zlib" / "recipe.yaml").write_text("recipe: {name: zlib}\n")
    script = base / "zlib" / "build.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    return base


def _module(data: bytes) -> types.ModuleType:
    mod = types.ModuleType(br.MODULE)
    mod.DIGEST = hashlib.sha256(data).hexdigest()
    mod.DATA = data
    return mod


@pytest.fixture
def embedded(tmp_path, monkeypatch):
    """Install a fake generated module holding a small recipes tree."""
    data = br.build_archive(_tree(tmp_path / "src"))
    mod = _module(data)
    monkeypatch.setitem(sys.modules, br.MODULE, mod)
    monkeypatch.setenv("CVCPKG_CACHE", str(tmp_path / "cache"))
    return mod


@pytest.fixture
def meipass(tmp_path, monkeypatch):
    """Run as the frozen binary, whose bootloader unpack dir is the result."""
    path = tmp_path / "meipass"
    path.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(path), raising=False)
    return path


def _deny(monkeypatch, tree):
    """Make listing *tree* fail, as for a tree another account unpacked.

    Only listing is denied: the real tree's ``.complete`` still stats as a
    file, which is what let the old marker check hand it back.
    """
    real = os.scandir

    def scandir(path="."):
        if isinstance(path, (str, os.PathLike)):
            p = Path(path)
            if p == tree or tree in p.parents:
                raise PermissionError(13, "Access is denied", str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)


def test_archive_is_reproducible_and_keeps_layout(tmp_path):
    src = _tree(tmp_path / "src")
    data = br.build_archive(src)
    assert br.build_archive(src) == data
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert names == [
        "recipes/_common/env.sh",
        "recipes/zlib/build.sh",
        "recipes/zlib/recipe.yaml",
    ]


def test_generated_module_round_trips(tmp_path, monkeypatch):
    out = br.write_module(_tree(tmp_path / "src"), tmp_path / "gen")
    monkeypatch.syspath_prepend(str(out.parent))
    # setitem first, so monkeypatch drops the imported module again afterwards.
    monkeypatch.setitem(sys.modules, br.MODULE, None)
    del sys.modules[br.MODULE]
    mod = importlib.import_module(br.MODULE)
    assert hashlib.sha256(mod.DATA).hexdigest() == mod.DIGEST
    got = br.embedded_recipes_dir(tmp_path / "cache")
    assert (got / "zlib" / "recipe.yaml").read_text() == "recipe: {name: zlib}\n"


def test_none_without_embedded_module(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, br.MODULE, None)  # import raises ImportError
    assert br.embedded_recipes_dir(tmp_path) is None


def test_find_recipes_dir_prefers_embedded(embedded, tmp_path):
    got = find_recipes_dir()
    assert got == tmp_path / "cache" / "bundled-recipes" / embedded.DIGEST[:16] / "recipes"
    assert (got / "_common" / "env.sh").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_unpack_restores_exec_bit(embedded):
    got = find_recipes_dir()
    assert os.access(got / "zlib" / "build.sh", os.X_OK)
    assert not os.access(got / "zlib" / "recipe.yaml", os.X_OK)


def test_second_start_reuses_the_tree(embedded, monkeypatch):
    first = find_recipes_dir()

    def boom(*a, **k):
        raise AssertionError("unpacked again")

    monkeypatch.setattr(br, "_unpack", boom)
    assert find_recipes_dir() == first


def test_lost_race_uses_the_winner(tmp_path, embedded, monkeypatch):
    # Another process finishes the same tree while this one is unpacking.
    root = tmp_path / "cache" / "bundled-recipes"
    target = root / embedded.DIGEST[:16]
    real_unpack = br._unpack

    def racing(data, dest):
        real_unpack(data, dest)
        if not target.exists():
            real_unpack(data, target)

    monkeypatch.setattr(br, "_unpack", racing)
    assert br.embedded_recipes_dir(root) == target / "recipes"
    assert [p.name for p in root.iterdir()] == [target.name]


def test_incomplete_target_is_replaced(tmp_path, embedded):
    root = tmp_path / "cache" / "bundled-recipes"
    stale = root / embedded.DIGEST[:16] / "recipes" / "_common"
    stale.mkdir(parents=True)  # no .complete marker
    got = br.embedded_recipes_dir(root)
    assert (got / "zlib" / "recipe.yaml").is_file()


def test_unwritable_cache_falls_back_to_meipass(tmp_path, embedded, monkeypatch):
    def refuse(*a, **k):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(br, "_unpack_into_cache", refuse)
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    got = br.embedded_recipes_dir(tmp_path / "cache")
    assert got.is_relative_to(meipass)
    assert (got / "zlib" / "recipe.yaml").is_file()


def test_unreadable_tree_falls_back_to_meipass(tmp_path, embedded, meipass, monkeypatch, capsys):
    # A complete tree that the elevated builder sharing the cache unpacked:
    # handing it back made load_all_recipes die with WinError 5.
    root = tmp_path / "cache" / "bundled-recipes"
    target = root / embedded.DIGEST[:16]
    br.embedded_recipes_dir(root)
    _deny(monkeypatch, target)
    got = br.embedded_recipes_dir(root)
    assert got.is_relative_to(meipass)
    assert (got / "zlib" / "recipe.yaml").is_file()
    assert str(target) in capsys.readouterr().err
    monkeypatch.undo()
    # The unreadable tree is left alone, and no copy is left in the cache.
    assert [p.name for p in root.iterdir()] == [target.name]


def test_unreadable_race_winner_leaves_no_temp_copy(tmp_path, embedded, meipass, monkeypatch):
    # Another account finishes the same tree while this process is unpacking.
    root = tmp_path / "cache" / "bundled-recipes"
    target = root / embedded.DIGEST[:16]
    real_unpack = br._unpack

    def racing(data, dest):
        real_unpack(data, dest)
        if not target.exists():
            real_unpack(data, target)
            _deny(monkeypatch, target)

    monkeypatch.setattr(br, "_unpack", racing)
    got = br.embedded_recipes_dir(root)
    assert got.is_relative_to(meipass)
    monkeypatch.undo()
    assert [p.name for p in root.iterdir()] == [target.name]


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")
def test_tree_admits_whoever_the_cache_admits(tmp_path, embedded):
    # The tree used to be a renamed tempfile.mkdtemp dir, whose protected ACL
    # (SYSTEM, Administrators, owner) shut out the same user unelevated when
    # an elevated process unpacked it.  Grant Users (BU) on the cache root and
    # check that the tree has it too, by SID in the SDDL that /save writes.
    root = tmp_path / "cache" / "bundled-recipes"
    root.mkdir(parents=True)
    grant = ["icacls", str(root), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"]
    subprocess.run(grant, capture_output=True, check=True)
    tree = br.embedded_recipes_dir(root).parent
    saved = tmp_path / "acl.txt"
    subprocess.run(["icacls", str(tree), "/save", str(saved)], capture_output=True, check=True)
    assert ";;;BU)" in saved.read_text(encoding="utf-16-le")  # no BOM


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_tree_is_not_private_to_its_owner(embedded):
    # A mkdtemp dir is 0700, so a tree root unpacked was unreadable to users.
    old = os.umask(0o022)
    try:
        tree = find_recipes_dir().parent
    finally:
        os.umask(old)
    assert stat.S_IMODE(tree.stat().st_mode) == 0o755


def test_prune_keeps_the_newest_trees(tmp_path, embedded):
    root = tmp_path / "cache" / "bundled-recipes"
    for i, name in enumerate(["old0", "old1", "old2"]):
        (root / name).mkdir(parents=True)
        marker = root / name / ".complete"
        marker.write_text("")
        os.utime(marker, (1000 + i, 1000 + i))
    stale_tmp = root / ".tmp-abandoned"
    stale_tmp.mkdir()
    os.utime(stale_tmp, (1000, 1000))
    br.embedded_recipes_dir(root)
    assert sorted(p.name for p in root.iterdir()) == sorted([embedded.DIGEST[:16], "old1", "old2"])


def test_cache_gc_leaves_bundled_recipes(embedded, tmp_path):
    from cvcpkg.cache import gc

    find_recipes_dir()
    assert gc(tmp_path / "cache", set()) == 0
    assert (tmp_path / "cache" / "bundled-recipes").is_dir()


def test_overlays_still_win_over_embedded(embedded, tmp_path, monkeypatch):
    # The embedded set is only the default dir: --recipes-dir overlays come
    # after it, and the later dir wins on a name conflict (load_all_recipes).
    from cvcpkg.validation import resolve_recipe_dirs

    overlay = tmp_path / "overlay"
    (overlay / "zlib").mkdir(parents=True)
    real = find_recipes_dir()
    (overlay / "zlib" / "recipe.yaml").write_text((real / "zlib" / "recipe.yaml").read_text())
    monkeypatch.chdir(tmp_path)
    dirs = resolve_recipe_dirs([overlay])
    assert dirs == [real, overlay.resolve()]
