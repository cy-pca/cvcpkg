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
    the components tuple it was invoked with."""
    with mock.patch("cvcpkg.cli._install.install") as inst:
        main(["install-deps", *args])
    assert inst.called, "install-deps must forward to install"
    return set(inst.call_args.kwargs["components"])


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
