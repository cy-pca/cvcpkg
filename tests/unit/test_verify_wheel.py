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
    data=True,
    vm_test_recipe=False,
    vm_test_script=True,
    data_ref_recipe=False,
    data_ref_file=True,
    entry_points=("cvcpkg", "cvcpkg-server"),
    name="cvcpkg",
    version="2.0.0",
    requires_python=True,
):
    with zipfile.ZipFile(path, "w") as z:
        # Drive the fixture off the checker's own lists, so adding a required
        # module/data file to verify_wheel.py cannot make these tests fail for
        # the uninteresting reason that the FIXTURE is out of date.
        vw = _load()
        if modules:
            for m in vw.REQUIRED_MODULES:
                z.writestr(m, "")
        if data:
            for d in vw.REQUIRED_DATA:
                z.writestr(d, "type: object\n")
        for r in recipes:
            z.writestr(f"cvcpkg/recipes/{r}/recipe.yaml", "schema_version: 1\n")
            if scripts:
                z.writestr(f"cvcpkg/recipes/{r}/build.sh", "#!/usr/bin/env bash\n")
        if vm_test_recipe:
            # An image recipe naming a guest-side script the bundle may or may
            # not carry — the exact shape that let haiku-image's vm-test.sh be
            # left out of a declared file list.
            z.writestr(
                "cvcpkg/recipes/some-image/recipe.yaml",
                "schema_version: 1\nbuild:\n  matrix:\n"
                "    - platform: linux\n      script: build.sh\n"
                "test:\n  vm:\n    script: vm-test.sh\n",
            )
            z.writestr("cvcpkg/recipes/some-image/build.sh", "#!/usr/bin/env bash\n")
            if vm_test_script:
                z.writestr("cvcpkg/recipes/some-image/vm-test.sh", "#!/bin/sh\n")
        if data_ref_recipe:
            # A recipe whose build.sh reads a sibling file that NOTHING
            # declares -- haiku-image's shape.  Only build.sh knows it exists.
            z.writestr("cvcpkg/recipes/img/recipe.yaml", "schema_version: 1\n")
            z.writestr(
                "cvcpkg/recipes/img/build.sh",
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                'RECIPE_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
                'cp "${RECIPE_DIR}/UserBootscript" "${INJ}/UserBootscript"\n',
            )
            if data_ref_file:
                z.writestr("cvcpkg/recipes/img/UserBootscript", "#!/bin/sh\n")
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


def test_missing_schema_data_flagged(tmp_path):
    """A wheel with every .py but no schemas/*.yaml is NOT publishable.

    The schemas ship only because pyproject names them and are read through
    importlib.resources, so this is the failure that imports cleanly and then
    blows up on the first `cvcpkg validate`.
    """
    mod = _load()
    whl = tmp_path / "cvcpkg-2.0.0-py3-none-any.whl"
    _make_wheel(whl, data=False)
    problems = mod.verify(whl, min_recipes=1)
    assert any("missing data file" in p for p in problems)
    assert any("image-schema.yaml" in p for p in problems)


def test_recipe_script_reference_must_be_bundled(tmp_path):
    """`test.vm.script: vm-test.sh` with no vm-test.sh beside it is flagged.

    cvcpkg resolves a recipe's scripts as ``recipe_dir / <script>`` on the
    builder, so a bundle that carries build.sh but not the guest-side script
    builds and then dies at the test step.
    """
    mod = _load()
    good = tmp_path / "good.whl"
    _make_wheel(good, vm_test_recipe=True, vm_test_script=True)
    assert [p for p in mod.verify(good, min_recipes=1) if "vm-test.sh" in p] == []

    bad = tmp_path / "bad.whl"
    _make_wheel(bad, vm_test_recipe=True, vm_test_script=False)
    problems = mod.verify(bad, min_recipes=1)
    assert any("recipe script not bundled" in p and "vm-test.sh" in p for p in problems)


def _src_tree(tmp_path, *, with_bootscript=True):
    """A stand-in source recipes/ tree for the data-file scan to consult."""
    src = tmp_path / "recipes"
    (src / "_common").mkdir(parents=True)  # what marks a real recipes root
    (src / "img").mkdir()
    (src / "img" / "build.sh").write_text("#!/usr/bin/env bash\n")
    if with_bootscript:
        (src / "img" / "UserBootscript").write_text("#!/bin/sh\n")
    return src


def test_recipe_data_file_reference_must_be_bundled(tmp_path):
    """A file build.sh reads out of ${RECIPE_DIR} must ride along with it.

    Nothing declares these -- haiku-image's README-import.md, UserBuildConfig
    and UserBootscript are named only by `cp` lines inside build.sh -- so an
    extension allowlist in the build backend dropped all three, and the build
    died on the first `cp` under `set -e`, an hour in, long after the Haiku
    cross-toolchain had been compiled.
    """
    mod = _load()
    src = _src_tree(tmp_path)

    good = tmp_path / "good.whl"
    _make_wheel(good, data_ref_recipe=True, data_ref_file=True)
    assert [
        p for p in mod.verify(good, min_recipes=1, recipes_root=src) if "UserBootscript" in p
    ] == []

    bad = tmp_path / "bad.whl"
    _make_wheel(bad, data_ref_recipe=True, data_ref_file=False)
    problems = mod.verify(bad, min_recipes=1, recipes_root=src)
    assert any(
        "recipe data file not bundled" in p and "UserBootscript" in p for p in problems
    ), problems


def test_reference_to_a_file_absent_from_the_source_tree_is_not_flagged(tmp_path):
    """A script naming a file that exists nowhere is a broken RECIPE.

    Packaging can neither cause nor cure that, so the publish gate must not
    report it as a bundling failure -- otherwise one dangling `source` line in
    an unbuildable recipe turns every release red.  (recipes/ca-bundle is
    exactly this: both its scripts source a `_common/build-vars.*` that has
    never existed in the tree.)
    """
    mod = _load()
    src = _src_tree(tmp_path, with_bootscript=False)

    whl = tmp_path / "dangling.whl"
    _make_wheel(whl, data_ref_recipe=True, data_ref_file=False)
    assert [
        p for p in mod.verify(whl, min_recipes=1, recipes_root=src) if "UserBootscript" in p
    ] == []


def test_data_file_scan_is_skipped_without_a_source_tree(tmp_path):
    """Run outside a checkout, the scan reports nothing rather than guessing."""
    mod = _load()
    whl = tmp_path / "nosrc.whl"
    _make_wheel(whl, data_ref_recipe=True, data_ref_file=False)
    problems = mod.verify(whl, min_recipes=1, recipes_root=tmp_path / "absent")
    assert [p for p in problems if "data file" in p] == []


def test_recipe_dir_file_refs_reads_every_idiom():
    """Each way the corpus names "the directory this script lives in"."""
    mod = _load()
    refs = mod.recipe_dir_file_refs(
        'cp "${RECIPE_DIR}/README-import.md" out\n'
        'cp "$RECIPE_DIR/UserBootscript" out\n'
        'cp "${CVC_RECIPE_DIR}/from-env" out\n'
        'source "${SCRIPT_DIR}/../_common/python-wheel.sh"\n'
        '. "$(dirname "$0")/../_common/stage-source.sh"\n'
        '. "$PSScriptRoot\\..\\_common\\python-wheel.ps1"\n'
    )
    assert refs == {
        "README-import.md",
        "UserBootscript",
        "from-env",
        "../_common/python-wheel.sh",
        "../_common/stage-source.sh",
        "../_common/python-wheel.ps1",
    }


def test_recipe_dir_file_refs_skips_what_it_cannot_resolve():
    """Interpolated and globbed paths are dropped whole, never truncated.

    ``env-${CVC_PLATFORM}.sh`` must not be recorded as a reference to a file
    literally named ``env-`` -- a truncated prefix would be a phantom missing
    file on every build.
    """
    mod = _load()
    refs = mod.recipe_dir_file_refs(
        'source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"\n'
        'cp "${RECIPE_DIR}"/*.patch out\n'
        'cp "${RECIPE_DIR}/real.patch" out\n'
    )
    assert refs == {"real.patch"}


def test_resolve_recipe_ref_stays_inside_the_recipes_root():
    mod = _load()
    rdir = "cvcpkg/recipes/img"
    assert mod.resolve_recipe_ref(rdir, "UserBootscript") == f"{rdir}/UserBootscript"
    assert (
        mod.resolve_recipe_ref(rdir, "../_common/env-linux.sh")
        == "cvcpkg/recipes/_common/env-linux.sh"
    )
    # Climbing out of recipes/ is not something packaging can preserve.
    assert mod.resolve_recipe_ref(rdir, "../../schemas/recipe-schema.yaml") is None
    assert mod.resolve_recipe_ref(rdir, "../../../../../etc/passwd") is None


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
