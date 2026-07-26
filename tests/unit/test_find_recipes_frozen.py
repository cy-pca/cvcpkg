# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""find_recipes_dir() must discover the recipes bundled into a frozen single-file
binary (PyInstaller sets sys._MEIPASS), so a self-contained `cvcpkg` executable
finds the standard recipes with no --recipes-dir.
"""

from __future__ import annotations

import sys

import pytest

from cvcpkg.builder import find_recipes_dir


def _bundle(base, subpath):
    """Create a recipes tree (with the required _common/ marker) under *base*."""
    recipes = base
    for part in subpath.split("/"):
        recipes = recipes / part
    (recipes / "_common").mkdir(parents=True)
    return recipes


@pytest.mark.parametrize("layout", ["cvcpkg/recipes", "recipes"])
def test_meipass_bundle_is_found_first(tmp_path, monkeypatch, layout):
    # A frozen binary extracts data under sys._MEIPASS; both the package-mirroring
    # layout (cvcpkg/recipes) and the bare (recipes) layout are accepted.
    recipes = _bundle(tmp_path, layout)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert find_recipes_dir() == recipes


def test_meipass_without_common_marker_is_ignored(tmp_path, monkeypatch):
    # A _MEIPASS dir lacking the _common/ marker is not a recipes dir; fall
    # through to the normal (repo/pip) discovery rather than returning junk.
    (tmp_path / "cvcpkg" / "recipes").mkdir(parents=True)  # no _common/
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    # Repo checkout still resolves via the walk-up path.
    assert (find_recipes_dir() / "_common").is_dir()


def test_no_meipass_uses_normal_discovery(monkeypatch):
    # Non-frozen (pip/repo): the _MEIPASS branch is skipped entirely.
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert (find_recipes_dir() / "_common").is_dir()
