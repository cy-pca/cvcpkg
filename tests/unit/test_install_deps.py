"""cvcpkg install-deps installs a recipe's build+runtime dependency closure
(not its host_tools) by forwarding the names to `install`. Platform-scoped deps
are filtered to the target platform."""

from unittest import mock

import yaml

from cvcpkg.cli import main


def _write_recipe(tmp_path):
    d = tmp_path / "mylib"
    d.mkdir()
    (d / "recipe.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "recipe": {"name": "mylib", "upstream_version": "1.0", "cvc_revision": 1},
                "depends": {
                    "build": [{"name": "buildonlydep"}],
                    "runtime": [
                        {"name": "zlib"},
                        {"name": "boost"},
                        {"name": "winonly", "platforms": ["windows"]},
                    ],
                    "host_tools": [{"name": "cmake"}, {"name": "ninja"}],
                },
                "source": {"type": "vendored", "path": "."},
                "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
            }
        )
    )
    return d


def _run_capturing_install(args):
    """Run `cvcpkg install-deps ...` with the underlying `install` mocked; return
    the union of every component it was invoked with.

    Cross-compilation splits host tools (resolved for the host platform) from
    target deps (resolved for the target) into separate `install` passes, so the
    set a caller cares about — which deps get forwarded at all — is the union
    across passes, not the last one.  Tests that need to know *which* platform a
    dep resolved against inspect ``call_args_list`` directly (see below)."""
    calls = _run_capturing_calls(args)
    comps: set[str] = set()
    for kwargs in calls:
        comps |= set(kwargs["components"])
    return comps


def _run_capturing_calls(args):
    """Run `cvcpkg install-deps ...` with `install` mocked; return one kwargs dict
    per `install` invocation, in call order."""
    with mock.patch("cvcpkg.cli._install.install") as inst:
        main(["install-deps", *args])
    assert inst.called, "install-deps must forward to install"
    return [c.kwargs for c in inst.call_args_list]


def test_forwards_build_and_runtime_deps_not_host_tools(tmp_path):
    comps = _run_capturing_install(
        [str(_write_recipe(tmp_path)), "--prefix", str(tmp_path / "deps"), "--platform", "linux"]
    )
    assert comps == {"buildonlydep", "zlib", "boost"}
    assert "cmake" not in comps and "ninja" not in comps  # host_tools excluded
    assert "winonly" not in comps  # windows-only dep filtered out on linux


def test_include_host_tools_flag(tmp_path):
    comps = _run_capturing_install(
        [str(_write_recipe(tmp_path)), "--platform", "linux", "--include-host-tools"]
    )
    assert {"cmake", "ninja"} <= comps


def test_platform_scoped_dep_included_on_its_platform(tmp_path):
    comps = _run_capturing_install([str(_write_recipe(tmp_path)), "--platform", "windows"])
    assert "winonly" in comps


def test_recipe_yaml_path_also_accepted(tmp_path):
    recipe = _write_recipe(tmp_path)
    comps = _run_capturing_install([str(recipe / "recipe.yaml"), "--platform", "linux"])
    assert comps == {"buildonlydep", "zlib", "boost"}


def test_recipe_resolved_by_name_via_recipes_dir(tmp_path):
    # Regression: resolving a recipe by NAME (not a path) routes through
    # _resolve_recipes_dirs, which crashed with `TypeError: ... takes from 0 to 1
    # positional arguments but 2 were given` because no_default (keyword-only) was
    # passed positionally. The path-based tests above never exercised this branch.
    _write_recipe(tmp_path)  # tmp_path/mylib/recipe.yaml
    comps = _run_capturing_install(
        ["mylib", "--recipes-dir", str(tmp_path), "--no-default-recipes", "--platform", "linux"]
    )
    assert comps == {"buildonlydep", "zlib", "boost"}


# ── Cross-compilation: depends.build host tools (cy-pca/cvcpkg#48) ──────────
#
# By repo convention host build tools (cmake, ninja) are declared under
# depends.build, not host_tools:.  Cross-compiling (e.g. --platform wasm) must
# not try to resolve them for the target — they have no wasm bundle — while the
# target's own libraries (which DO have a wasm build) must still resolve.


def _matrix(*platforms):
    return [{"platform": p, "script": f"build-{p}.sh"} for p in platforms]


def _write_cross_recipes(tmp_path):
    """A recipes dir modelling the VTK case: cmake/ninja (host-only) under
    depends.build alongside a target-buildable build dep, plus runtime libs."""
    rdir = tmp_path / "recipes"
    rdir.mkdir()

    def _recipe(name, *, matrix, depends=None):
        d = rdir / name
        d.mkdir()
        body = {
            "schema_version": 1,
            "recipe": {"name": name, "upstream_version": "1.0", "cvc_revision": 1},
            "source": {"type": "vendored", "path": "."},
            "build": {"matrix": matrix},
        }
        if depends is not None:
            body["depends"] = depends
        (d / "recipe.yaml").write_text(yaml.safe_dump(body))

    # Host build tools: no wasm entry -> classified as host tools when cross-compiling.
    _recipe("cmake", matrix=_matrix("linux", "macos", "windows"))
    _recipe("ninja", matrix=_matrix("linux", "macos", "windows"))
    # A build dep that DOES build for wasm -> stays a target dep, not a host tool.
    _recipe("wasmgen", matrix=_matrix("linux", "macos", "windows", "wasm"))
    # Runtime libs with a wasm build.
    _recipe("zlib", matrix=_matrix("linux", "windows", "wasm"))
    _recipe("tiff", matrix=_matrix("linux", "windows", "wasm"))

    _recipe(
        "crossapp",
        matrix=_matrix("linux", "macos", "windows", "wasm"),
        depends={
            "build": ["cmake", "ninja", "wasmgen"],
            "runtime": ["zlib", "tiff"],
            "host_tools": [],
        },
    )
    return rdir


def _find_call(calls, platform):
    """The single install pass invoked for *platform* (fails if not exactly one)."""
    matches = [c for c in calls if c["platform"] == platform]
    assert len(matches) == 1, f"expected one install pass for {platform}, got {len(matches)}"
    return matches[0]


def test_cross_compile_excludes_depends_build_host_tools(tmp_path):
    # The reported bug: `install-deps <recipe> --platform wasm` died resolving
    # cmake/ninja (host tools under depends.build) for wasm.  They must be
    # excluded, while target-buildable deps (wasmgen, zlib, tiff) still resolve.
    rdir = _write_cross_recipes(tmp_path)
    calls = _run_capturing_calls(
        [
            "crossapp",
            "--recipes-dir",
            str(rdir),
            "--no-default-recipes",
            "--platform",
            "wasm",
            "--arch",
            "wasm32",
            "--host-platform",
            "linux",
        ]
    )
    assert len(calls) == 1, "no host-tools pass without --include-host-tools"
    target = _find_call(calls, "wasm")
    comps = set(target["components"])
    assert comps == {"wasmgen", "zlib", "tiff"}
    assert "cmake" not in comps and "ninja" not in comps
    assert target["arch"] == "wasm32"


def test_cross_compile_include_host_tools_resolves_for_host(tmp_path):
    # --include-host-tools installs the depends.build host tools too, but resolved
    # for the HOST platform (their only build), in a separate pass from the target.
    rdir = _write_cross_recipes(tmp_path)
    calls = _run_capturing_calls(
        [
            "crossapp",
            "--recipes-dir",
            str(rdir),
            "--no-default-recipes",
            "--platform",
            "wasm",
            "--arch",
            "wasm32",
            "--host-platform",
            "linux",
            "--include-host-tools",
        ]
    )
    assert len(calls) == 2
    target = _find_call(calls, "wasm")
    assert set(target["components"]) == {"wasmgen", "zlib", "tiff"}
    host = _find_call(calls, "linux")
    assert {"cmake", "ninja"} <= set(host["components"])
    assert "wasmgen" not in host["components"]  # target lib does not leak to host pass


def test_native_build_keeps_depends_build_tools(tmp_path):
    # Native build (host == target): depends.build tools resolve for the target
    # like any other dep — no reclassification, one pass, prior behaviour intact.
    rdir = _write_cross_recipes(tmp_path)
    calls = _run_capturing_calls(
        [
            "crossapp",
            "--recipes-dir",
            str(rdir),
            "--no-default-recipes",
            "--platform",
            "linux",
            "--host-platform",
            "linux",
        ]
    )
    assert len(calls) == 1
    comps = set(_find_call(calls, "linux")["components"])
    assert {"cmake", "ninja", "wasmgen", "zlib", "tiff"} == comps
