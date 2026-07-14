"""Tests for scripts/recipe_coverage.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("yaml")

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("recipe_coverage", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _mk_recipe(recipes_dir: Path, name: str, platforms: list[str]) -> None:
    d = recipes_dir / name
    d.mkdir(parents=True)
    matrix = "\n".join(f"    - platform: {p}\n      script: build.sh" for p in platforms)
    (d / "recipe.yaml").write_text(
        "schema_version: 1\n"
        f"recipe:\n  name: {name}\n  upstream_version: '1.0'\n  cvc_revision: 1\n"
        "source:\n  type: tarball\n"
        f"build:\n  matrix:\n{matrix}\n"
        "package:\n  files:\n    - lib/\n"
    )


def test_collect_and_summarize(tmp_path):
    mod = _load()
    r = tmp_path / "recipes"
    _mk_recipe(r, "a", ["linux", "macos", "windows"])
    _mk_recipe(r, "b", ["linux"])

    cov = mod.collect(r)
    assert cov == {"a": {"linux", "macos", "windows"}, "b": {"linux"}}

    counts = mod.summarize(cov)
    assert counts["linux"] == 2
    assert counts["macos"] == 1
    assert counts["cosmo"] == 0

    assert mod.missing_for(cov, "macos") == ["b"]
    assert mod.missing_for(cov, "linux") == []


def test_require_gate_exit_codes(tmp_path):
    mod = _load()
    r = tmp_path / "recipes"
    _mk_recipe(r, "a", ["linux"])
    _mk_recipe(r, "b", ["windows"])

    # b lacks linux → gate fails.
    assert mod.main(["--recipes-dir", str(r), "--require", "linux"]) == 1
    # require is AND across platforms — a lacks windows, b lacks linux.
    assert mod.main(["--recipes-dir", str(r), "--require", "linux,windows"]) == 1

    # A dir where every recipe builds both required platforms → gate passes.
    ok = tmp_path / "recipes_ok"
    _mk_recipe(ok, "x", ["linux", "windows"])
    _mk_recipe(ok, "y", ["linux", "windows"])
    assert mod.main(["--recipes-dir", str(ok), "--require", "linux,windows"]) == 0


def test_missing_recipes_dir_errors(tmp_path):
    mod = _load()
    assert mod.main(["--recipes-dir", str(tmp_path / "nope")]) == 2
