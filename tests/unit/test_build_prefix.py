"""Build-prefix separation: the build-dependency closure (host tools,
cross-toolchains, staged source packages) installs into a separate prefix so the
deliverable --prefix ships only the runtime closure.

The env-level behaviour is unit-tested here: PATH ordering, ${PREFIX} resolution
of cross-toolchain env, the CVC_BUILD_PREFIX handed to build scripts, and the
lib search path.  Placement itself is decided by the dependency edge -- see
tests/unit/test_dep_closures.py.  The CLI wires ``--build-prefix`` (default
``<prefix>.build``) into the build flow.
"""

from __future__ import annotations

import os

import yaml

from cvcpkg.builder import BuildContext, Recipe, _build_env

_RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
    "source": {"type": "tarball", "url": "file:///x.tgz", "sha256": "0" * 64},
    "build": {"matrix": [{"platform": "windows", "script": "build.ps1"}]},
}


def _recipe(tmp_path):
    d = tmp_path / "recipes" / "tp"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(_RECIPE))
    return Recipe.load(d)


def _ctx(tmp_path, recipe, *, build_prefix=None, cross=None):
    work = tmp_path / "work"
    return BuildContext(
        recipe=recipe,
        platform="windows",
        config="release",
        link="shared",
        prefix=tmp_path / "prefix",
        source_dir=work / "src",
        build_dir=work / "build",
        install_dir=work / "install",
        work_dir=work,
        host_platform="linux",
        cross_toolchain_env=cross or {},
        build_prefix=build_prefix,
    )


class TestBuildPrefix:
    def test_toolchain_env_resolves_to_build_prefix(self, tmp_path):
        r = _recipe(tmp_path)
        bp = tmp_path / "bp"
        ctx = _ctx(tmp_path, r, build_prefix=bp, cross={"CVC_BAZEL_BIN": "${PREFIX}/bin/bazel"})
        env = _build_env(ctx, r.build_matrix[0])
        # ${PREFIX} is a literal substitution, so the separators after the
        # prefix follow the recipe template ("/") rather than os.sep -- assert
        # exactly what the substitution yields (portable across Windows).
        assert env["CVC_BAZEL_BIN"] == str(bp) + "/bin/bazel"
        # ...and it points at the build prefix, NOT the deliverable prefix
        assert str(tmp_path / "prefix") not in env["CVC_BAZEL_BIN"]

    def test_build_prefix_bin_is_first_on_path(self, tmp_path):
        r = _recipe(tmp_path)
        bp = tmp_path / "bp"
        ctx = _ctx(tmp_path, r, build_prefix=bp)
        env = _build_env(ctx, r.build_matrix[0])
        dirs = env["PATH"].split(os.pathsep)
        assert dirs[0] == str((bp / "bin").resolve())
        # the deliverable prefix bin is still present (for built runtime deps)
        assert str((tmp_path / "prefix" / "bin").resolve()) in dirs

    def test_cvc_build_prefix_is_exported(self, tmp_path):
        # Build scripts get two roots: CVC_DEPS_PREFIX (runtime closure, ships)
        # and CVC_BUILD_PREFIX (build closure: host tools + src/<name>).
        r = _recipe(tmp_path)
        bp = tmp_path / "bp"
        ctx = _ctx(tmp_path, r, build_prefix=bp)
        env = _build_env(ctx, r.build_matrix[0])
        assert env["CVC_BUILD_PREFIX"] == str(bp.resolve())
        assert env["CVC_DEPS_PREFIX"] == str(tmp_path / "prefix")
        assert env["CVC_BUILD_PREFIX"] != env["CVC_DEPS_PREFIX"]

    def test_build_prefix_lib_is_searchable(self, tmp_path):
        # A build-closure tool may link its own shared libs.
        r = _recipe(tmp_path)
        bp = tmp_path / "bp"
        ctx = _ctx(tmp_path, r, build_prefix=bp)
        env = _build_env(ctx, r.build_matrix[0])
        key = "DYLD_LIBRARY_PATH" if os.uname().sysname == "Darwin" else "LD_LIBRARY_PATH"
        assert str((bp / "lib").resolve()) in env.get(key, "")

    def test_fallback_to_prefix_when_not_separated(self, tmp_path):
        # build_prefix=None -> legacy behaviour: everything under --prefix
        r = _recipe(tmp_path)
        ctx = _ctx(tmp_path, r, build_prefix=None, cross={"CVC_BAZEL_BIN": "${PREFIX}/bin/bazel"})
        env = _build_env(ctx, r.build_matrix[0])
        assert env["CVC_BAZEL_BIN"] == str(tmp_path / "prefix") + "/bin/bazel"
        assert env["CVC_BUILD_PREFIX"] == str((tmp_path / "prefix").resolve())

    def test_cli_default_build_prefix_is_sibling(self):
        # mirrors the CLI default: <prefix>.build beside --prefix
        from pathlib import Path

        prefix = Path("/tmp/mydeliverable").resolve()
        default = prefix.with_name(prefix.name + ".build")
        assert default == Path("/tmp/mydeliverable.build").resolve()
        assert default != prefix
