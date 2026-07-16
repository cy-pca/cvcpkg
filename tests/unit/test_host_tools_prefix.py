"""Host-tools prefix separation: build-time host tools install into a separate
prefix so the deliverable --prefix stays clean.

The env-level behaviour (PATH ordering + ${PREFIX} resolution of cross-toolchain
env against the host-tools prefix) is unit-tested here; the CLI wires
``--host-tools-prefix`` (default ``<prefix>.host-tools``) into the build flow.
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


def _ctx(tmp_path, recipe, *, host_tools_prefix=None, cross=None):
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
        host_tools_prefix=host_tools_prefix,
    )


class TestHostToolsPrefix:
    def test_toolchain_env_resolves_to_host_tools_prefix(self, tmp_path):
        r = _recipe(tmp_path)
        ht = tmp_path / "ht"
        ctx = _ctx(
            tmp_path, r, host_tools_prefix=ht, cross={"CVC_BAZEL_BIN": "${PREFIX}/bin/bazel"}
        )
        env = _build_env(ctx, r.build_matrix[0])
        # ${PREFIX} is a literal substitution, so the separators after the
        # prefix follow the recipe template ("/") rather than os.sep -- assert
        # exactly what the substitution yields (portable across Windows).
        assert env["CVC_BAZEL_BIN"] == str(ht) + "/bin/bazel"
        # ...and it points at the host-tools prefix, NOT the deliverable prefix
        assert str(tmp_path / "prefix") not in env["CVC_BAZEL_BIN"]

    def test_host_tools_bin_is_first_on_path(self, tmp_path):
        r = _recipe(tmp_path)
        ht = tmp_path / "ht"
        ctx = _ctx(tmp_path, r, host_tools_prefix=ht)
        env = _build_env(ctx, r.build_matrix[0])
        dirs = env["PATH"].split(os.pathsep)
        assert dirs[0] == str((ht / "bin").resolve())
        # the deliverable prefix bin is still present (for built deps)
        assert str((tmp_path / "prefix" / "bin").resolve()) in dirs

    def test_fallback_to_prefix_when_not_separated(self, tmp_path):
        # host_tools_prefix=None -> legacy behaviour: everything under --prefix
        r = _recipe(tmp_path)
        ctx = _ctx(
            tmp_path, r, host_tools_prefix=None, cross={"CVC_BAZEL_BIN": "${PREFIX}/bin/bazel"}
        )
        env = _build_env(ctx, r.build_matrix[0])
        assert env["CVC_BAZEL_BIN"] == str(tmp_path / "prefix") + "/bin/bazel"

    def test_cli_default_host_tools_prefix_is_sibling(self):
        # mirrors the CLI default: <prefix>.host-tools beside --prefix
        from pathlib import Path

        prefix = Path("/tmp/mydeliverable").resolve()
        default = prefix.with_name(prefix.name + ".host-tools")
        assert default == Path("/tmp/mydeliverable.host-tools").resolve()
        assert default != prefix
