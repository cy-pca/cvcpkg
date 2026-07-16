"""_build_env must export CVC_BUILD_TYPE alongside CMAKE_BUILD_TYPE.

The _common/env-*.sh recipe helpers derive CMAKE_BUILD_TYPE from
CVC_BUILD_TYPE (defaulting to Release when unset), clobbering whatever the
harness put in CMAKE_BUILD_TYPE.  Regression: debug builds silently produced
Release binaries because the harness set only CMAKE_BUILD_TYPE/CVC_CONFIG.
"""

from __future__ import annotations

import yaml

from cvcpkg.builder import BuildContext, Recipe, _build_env

_RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
    "source": {"type": "tarball", "url": "file:///x.tgz", "sha256": "0" * 64},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
}


def _recipe(tmp_path):
    d = tmp_path / "recipes" / "tp"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(_RECIPE))
    return Recipe.load(d)


def _env_for(tmp_path, config):
    r = _recipe(tmp_path)
    work = tmp_path / "work"
    ctx = BuildContext(
        recipe=r,
        platform="linux",
        config=config,
        link="shared",
        prefix=tmp_path / "prefix",
        source_dir=work / "src",
        build_dir=work / "build",
        install_dir=work / "install",
        work_dir=work,
    )
    return _build_env(ctx, r.build_matrix[0])


class TestBuildEnvBuildType:
    def test_debug_config_exports_debug_build_type(self, tmp_path):
        env = _env_for(tmp_path, "debug")
        assert env["CMAKE_BUILD_TYPE"] == "Debug"
        assert env["CVC_BUILD_TYPE"] == "Debug"
        assert env["CVC_CONFIG"] == "debug"

    def test_release_config_exports_release_build_type(self, tmp_path):
        env = _env_for(tmp_path, "release")
        assert env["CMAKE_BUILD_TYPE"] == "Release"
        assert env["CVC_BUILD_TYPE"] == "Release"
        assert env["CVC_CONFIG"] == "release"
