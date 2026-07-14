"""Tests for packaging/verify_wheel.py."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "verify_wheel.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_wheel", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _make_wheel(
    path: Path,
    *,
    recipes=("zlib", "boost", "openssl"),
    scripts=True,
    modules=True,
    entry_points=("cvcpkg", "cvcpkg-server"),
    name="cvcpkg",
    version="2.0.0",
    requires_python=True,
):
    with zipfile.ZipFile(path, "w") as z:
        if modules:
            z.writestr("cvcpkg/__init__.py", "")
            z.writestr("cvcpkg/cli/__init__.py", "")
            z.writestr("cvcpkg/server/app.py", "")
        for r in recipes:
            z.writestr(f"cvcpkg/recipes/{r}/recipe.yaml", "schema_version: 1\n")
            if scripts:
                z.writestr(f"cvcpkg/recipes/{r}/build.sh", "#!/usr/bin/env bash\n")
        dist = f"{name}-{version}.dist-info"
        ep = "[console_scripts]\n" + "".join(f"{e}=cvcpkg.x:y\n" for e in entry_points)
        z.writestr(f"{dist}/entry_points.txt", ep)
        md = f"Name: {name}\nVersion: {version}\n"
        if requires_python:
            md += "Requires-Python: >=3.10,<4.0\n"
        z.writestr(f"{dist}/METADATA", md)


def test_good_wheel_passes(tmp_path):
    mod = _load()
    whl = tmp_path / "cvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(whl, recipes=tuple(f"pkg{i}" for i in range(60)) + ("zlib",))
    assert mod.verify(whl, min_recipes=50) == []


def test_no_recipes_flagged(tmp_path):
    mod = _load()
    whl = tmp_path / "cvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(whl, recipes=())
    problems = mod.verify(whl, min_recipes=50)
    assert any("recipes bundled" in p for p in problems)
    assert any("zlib" in p for p in problems)
    assert any("build scripts" in p for p in problems)


def test_missing_entry_point_flagged(tmp_path):
    mod = _load()
    whl = tmp_path / "cvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(whl, entry_points=("cvcpkg",))  # server entry point missing
    problems = mod.verify(whl, min_recipes=1)
    assert any("cvcpkg-server" in p for p in problems)


def test_missing_module_flagged(tmp_path):
    mod = _load()
    whl = tmp_path / "cvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(whl, modules=False)
    assert any("missing module" in p for p in mod.verify(whl, min_recipes=1))


def test_wrong_metadata_name_flagged(tmp_path):
    mod = _load()
    whl = tmp_path / "notcvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(whl, name="notcvcpkg", requires_python=False)
    problems = mod.verify(whl, min_recipes=1)
    assert any("Name" in p for p in problems)
    assert any("Requires-Python" in p for p in problems)


def test_main_exit_codes(tmp_path):
    mod = _load()
    good = tmp_path / "cvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(good, recipes=tuple(f"pkg{i}" for i in range(60)) + ("zlib",))
    assert mod.main([str(good), "--min-recipes", "50"]) == 0

    bad = tmp_path / "cvcpkg-2.0.0-bad.whl"
    _make_wheel(bad, recipes=())
    assert mod.main([str(bad), "--min-recipes", "50"]) == 1

    assert mod.main([str(tmp_path / "nope.whl")]) == 2
