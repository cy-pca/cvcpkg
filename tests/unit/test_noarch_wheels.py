"""An any/noarch bundle must not carry a platform-specific wheel.

pyrsistent-cp312 0.20.0+cvc.2 was published as any/noarch, built on a macOS
runner, with this in its installed dist-info:

    Tag: cp312-cp312-macosx_13_0_arm64

and pvectorc.cpython-312-darwin.so beside it.  Its sdist compiles an optional
C extension whenever a compiler is there, and the generator called the column
pure because PyPI also has a py3-none-any wheel.  Every Windows and Linux
consumer got the macOS binary.  Packing now refuses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import BuildError, check_noarch_wheels, enforce_noarch_wheels, pack_from_prefix

SITE = "lib/python3.12/site-packages"


def _dist_info(prefix: Path, name: str, *tags: str) -> None:
    d = prefix / SITE / f"{name}-1.0.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    body = "Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\n"
    body += "".join(f"Tag: {t}\n" for t in tags)
    (d / "WHEEL").write_text(body, encoding="utf-8")


def test_pure_wheels_pass(tmp_path):
    _dist_info(tmp_path, "click", "py3-none-any")
    _dist_info(tmp_path, "six", "py2-none-any", "py3-none-any")
    assert check_noarch_wheels(tmp_path) == []


def test_a_platform_wheel_is_reported_with_its_tag(tmp_path):
    _dist_info(tmp_path, "click", "py3-none-any")
    _dist_info(tmp_path, "pyrsistent", "cp312-cp312-macosx_13_0_arm64")
    assert check_noarch_wheels(tmp_path) == [
        f"{SITE}/pyrsistent-1.0.dist-info: cp312-cp312-macosx_13_0_arm64"
    ]


def test_only_noarch_packs_are_checked(tmp_path):
    _dist_info(tmp_path, "pyrsistent", "cp312-cp312-win_amd64")
    enforce_noarch_wheels(tmp_path, "windows", "pyrsistent-cp312")
    with pytest.raises(BuildError, match=r"cp312-cp312-win_amd64"):
        enforce_noarch_wheels(tmp_path, "any", "pyrsistent-cp312")


def _noarch_recipe(tmp_path: Path) -> Path:
    d = tmp_path / "recipes" / "pyrsistent-cp312"
    d.mkdir(parents=True)
    recipe = {
        "schema_version": 1,
        "recipe": {"name": "pyrsistent-cp312", "upstream_version": "1.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "third-party/x"},
        "patches": [],
        "build": {"matrix": [{"platform": "any", "script": "build.sh"}]},
        "package": {"files": [f"{SITE}/*.dist-info/"]},
    }
    (d / "recipe.yaml").write_text(yaml.dump(recipe), encoding="utf-8")
    return d


def test_pack_from_prefix_refuses_a_platform_wheel_in_a_noarch_bundle(tmp_path):
    prefix = tmp_path / "prefix"
    _dist_info(prefix, "pyrsistent", "cp312-cp312-macosx_13_0_arm64")
    with pytest.raises(BuildError, match=r"platform-specific wheel"):
        pack_from_prefix(
            _noarch_recipe(tmp_path), prefix, platform="any", output_dir=tmp_path / "dist"
        )
    assert not (tmp_path / "dist").exists()


def test_pack_from_prefix_packs_a_pure_noarch_bundle(tmp_path):
    prefix = tmp_path / "prefix"
    _dist_info(prefix, "pyrsistent", "py3-none-any")
    archive, _, _ = pack_from_prefix(
        _noarch_recipe(tmp_path), prefix, platform="any", output_dir=tmp_path / "dist"
    )
    assert archive.is_file()
