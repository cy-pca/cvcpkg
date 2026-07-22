"""`cvcpkg validate` over an arbitrary set of recipe directories.

The validator + its schemas now ship inside the cvcpkg package, so a downstream
repo can validate its own recipes/ without a libcvc-deps checkout.  These tests
pin the new surface: schema loading from the package, path/keyword targets, the
--recipes-dir overlay (later-dir-wins), --no-default-recipes, and the
cross-recipe missing-dependency check over the merged set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("cvcpkg.semver", reason="needs cvcpkg on the path")

from cvcpkg import validation  # noqa: E402


def _write_recipe(
    root: Path,
    name: str,
    *,
    version: str = "1.0",
    revision: int = 1,
    provides: list[str] | None = None,
    build_deps: list[str] | None = None,
    with_script: bool = True,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    lines = [
        "schema_version: 1",
        "recipe:",
        f"  name: {name}",
        f'  upstream_version: "{version}"',
        f"  cvc_revision: {revision}",
        "  maintainer: x",
        "  maintainer_email: x@x",
        "  homepage: http://x",
        "  license: MIT",
        '  description: "x"',
    ]
    if provides:
        lines.append(f"provides: [{', '.join(provides)}]")
    lines.append("source: {type: vendored, path: x}")
    if build_deps:
        lines.append("depends:")
        lines.append("  build:")
        for dep in build_deps:
            lines.append(f"    - name: {dep}")
    lines.append("build: {matrix: [{platform: linux, script: build.sh}]}")
    lines.append("package: {files: ['bin/x']}")
    (d / "recipe.yaml").write_text("\n".join(lines) + "\n")
    if with_script:
        (d / "build.sh").write_text("#!/bin/sh\n")
    return d


def test_load_schema_from_package():
    # No repo checkout needed: schemas resolve via importlib.resources.
    for kind in ("recipe", "components", "manifest"):
        assert isinstance(validation.load_schema(kind), dict)


def test_single_recipe_path_clean(tmp_path):
    r = tmp_path / "recipes"
    _write_recipe(r, "solo")
    errors = validation.run(str(r / "solo"), no_default=True)
    assert errors == [], errors


def test_cross_dep_resolves_across_two_overlay_dirs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_recipe(a, "foo", build_deps=["bar"])
    _write_recipe(b, "bar")
    # bar lives in the second overlay dir → foo's dep resolves over the merge.
    errors = validation.run("recipes", extra_dirs=[a, b], no_default=True)
    assert errors == [], errors


def test_cross_dep_missing_is_reported(tmp_path):
    a = tmp_path / "a"
    _write_recipe(a, "foo", build_deps=["bar"])
    errors = validation.run("recipes", extra_dirs=[a], no_default=True)
    assert any("unknown dependency 'bar'" in e for e in errors), errors


def test_single_path_resolves_siblings(tmp_path):
    # Pointing at one recipe still resolves its siblings in the same dir
    # (e.g. cvc-cli depends on its sibling libcvc).
    r = tmp_path / "recipes"
    _write_recipe(r, "cli", build_deps=["lib"])
    _write_recipe(r, "lib")
    errors = validation.run(str(r / "cli"), no_default=True)
    assert errors == [], errors


def test_dep_on_provided_slot_resolves(tmp_path):
    r = tmp_path / "recipes"
    _write_recipe(r, "consumer", build_deps=["libcvc"])
    _write_recipe(r, "libcvc-cuda", provides=["libcvc"])
    errors = validation.run("recipes", extra_dirs=[r], no_default=True)
    assert errors == [], errors


def test_later_dir_wins(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_recipe(a, "pkg", version="1.0")
    _write_recipe(b, "pkg", version="2.0")
    rmap = validation.build_recipe_map([a, b])
    assert rmap["pkg"] == b / "pkg"


def test_missing_build_script_reported(tmp_path):
    r = tmp_path / "recipes"
    _write_recipe(r, "noscript", with_script=False)
    errors = validation.run(str(r / "noscript"), no_default=True)
    assert any("build script 'build.sh' not found" in e for e in errors), errors


def test_unparseable_version_reported(tmp_path):
    r = tmp_path / "recipes"
    _write_recipe(r, "badver", version="1.0stable")
    errors = validation.run(str(r / "badver"), no_default=True)
    assert any("orderable" in e for e in errors), errors


def test_no_default_flags_external_deps(tmp_path):
    # With --no-default-recipes and no overlay providing them, a recipe's deps
    # on external packages (boost/cmake/…) are flagged — the opt-in behaviour.
    r = tmp_path / "recipes"
    _write_recipe(r, "app", build_deps=["boost"])
    errors = validation.run(str(r / "app"), no_default=True)
    assert any("unknown dependency 'boost'" in e for e in errors), errors


def test_report_exit_codes(capsys):
    assert validation.report([]) == 0
    assert validation.report(["boom"]) == 1
