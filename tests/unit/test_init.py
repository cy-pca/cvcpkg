"""Tests for `cvcpkg init` recipe scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.cli import main

jsonschema = pytest.importorskip("jsonschema")

# The recipe schema now ships inside the cvcpkg package.
from cvcpkg.validation import load_schema  # noqa: E402


def _validate_recipe(recipe_yaml: Path) -> None:
    schema = load_schema("recipe")
    doc = yaml.safe_load(recipe_yaml.read_text())
    jsonschema.Draft202012Validator(schema).validate(doc)


@pytest.mark.parametrize("build_system", ["cmake", "meson", "autotools"])
def test_init_scaffolds_schema_valid_recipe(tmp_path, build_system):
    ret = main(
        [
            "init",
            "mylib",
            "--dir",
            str(tmp_path / "recipes"),
            "--build-system",
            build_system,
            "--version",
            "1.2.3",
        ]
    )
    assert ret == 0
    d = tmp_path / "recipes" / "mylib"
    assert (d / "recipe.yaml").is_file()
    assert (d / "build.sh").is_file()
    # Windows build script only for the cmake scaffold.
    assert (d / "build.ps1").is_file() == (build_system == "cmake")

    _validate_recipe(d / "recipe.yaml")

    doc = yaml.safe_load((d / "recipe.yaml").read_text())
    assert doc["recipe"]["name"] == "mylib"
    assert doc["recipe"]["upstream_version"] == "1.2.3"
    scripts = {e["script"] for e in doc["build"]["matrix"]}
    assert "build.sh" in scripts


def test_init_refuses_existing_without_force(tmp_path):
    args = ["init", "mylib", "--dir", str(tmp_path / "recipes")]
    assert main(args) == 0
    # Second run without --force fails.
    assert main(args) == 1
    # With --force it succeeds.
    assert main([*args, "--force"]) == 0


def test_init_rejects_invalid_name(tmp_path):
    assert main(["init", "1bad", "--dir", str(tmp_path / "recipes")]) == 1
    assert not (tmp_path / "recipes" / "1bad").exists()
