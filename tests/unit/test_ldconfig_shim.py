"""Builds must not rewrite the builder VM's shared-library cache.

On OpenBSD (and FreeBSD) ldconfig(8) maintains a system-wide hints file and
REBUILDS IT FROM SCRATCH by default -- only ``-m`` merges.  A build step that
runs ldconfig as root there rewrites machine-global state: libtool's
finish_cmds (``ldconfig -m $libdir``) litter the hints with the job's own
scratch directories, and a bare ``ldconfig`` drops /usr/local/lib outright,
after which nothing installed under /usr/local resolves -- including python:

    ld.so: python3.12: can't load library 'libpython3.12.so.0.0'

That took out the openbsd-x86_64 column of every cvcpkg-v* release.  cvcpkg
never needs the system hints updated (it exports the prefix search paths
itself), so the fix is to put a no-op ldconfig on the build's PATH.
"""

from __future__ import annotations

import os
import subprocess

import pytest
import yaml

from cvcpkg.builder import BuildContext, Recipe, _build_env

_RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
    "source": {"type": "tarball", "url": "file:///x.tgz", "sha256": "0" * 64},
    "build": {
        "matrix": [
            {"platform": "openbsd", "script": "build.sh"},
            {"platform": "freebsd", "script": "build.sh"},
            {"platform": "netbsd", "script": "build.sh"},
            {"platform": "linux", "script": "build.sh"},
        ]
    },
}


def _recipe(tmp_path):
    d = tmp_path / "recipes" / "tp"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(_RECIPE))
    return Recipe.load(d)


def _ctx_env(tmp_path, platform, *, build_dir=None, prefix=None):
    r = _recipe(tmp_path)
    work = tmp_path / "work"
    prefix = prefix or tmp_path / "prefix"
    entry = next(e for e in r.build_matrix if e.platform == platform)
    ctx = BuildContext(
        recipe=r,
        platform=platform,
        config="release",
        link="shared",
        prefix=prefix,
        source_dir=work / "src",
        build_dir=build_dir or work / "build",
        install_dir=work / "install",
        work_dir=work,
    )
    return ctx, _build_env(ctx, entry)


def _first_ldconfig_on(path_value):
    """Resolve ldconfig the way a build's shell would, honouring PATH order."""
    for d in path_value.split(os.pathsep):
        candidate = os.path.join(d, "ldconfig")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class TestLdconfigShim:
    def test_openbsd_build_resolves_ldconfig_to_the_shim(self, tmp_path):
        _, env = _ctx_env(tmp_path, "openbsd")
        found = _first_ldconfig_on(env["PATH"])
        assert found is not None, "no ldconfig on PATH"
        assert found.endswith(os.path.join(".cvcpkg-shims", "ldconfig"))

    def test_freebsd_is_covered_too(self, tmp_path):
        _, env = _ctx_env(tmp_path, "freebsd")
        found = _first_ldconfig_on(env["PATH"])
        assert found is not None
        assert ".cvcpkg-shims" in found

    def test_shim_precedes_the_real_ldconfig_even_when_sbin_is_appended(self, tmp_path):
        """libtool runs `PATH="$PATH:/sbin" ldconfig -m $libdir`.

        Appending /sbin must not let the real binary win, so the shim has to
        sit at the very front -- ahead of the prefix bin dirs as well.
        """
        _, env = _ctx_env(tmp_path, "openbsd")
        libtool_path = env["PATH"] + os.pathsep + "/sbin"
        found = _first_ldconfig_on(libtool_path)
        assert found is not None
        assert ".cvcpkg-shims" in found

    def test_netbsd_has_no_hints_cache_so_no_shim(self, tmp_path):
        """NetBSD ships no /var/run/ld.so.hints and no /etc/ld.so.conf."""
        ctx, env = _ctx_env(tmp_path, "netbsd")
        assert not (ctx.build_dir / ".cvcpkg-shims").exists()

    def test_linux_is_untouched(self, tmp_path):
        ctx, env = _ctx_env(tmp_path, "linux")
        assert not (ctx.build_dir / ".cvcpkg-shims").exists()

    @pytest.mark.skipif(
        os.name == "nt",
        reason="the shim is a /bin/sh script; it is only ever installed on BSD builders",
    )
    def test_shim_is_a_silent_no_op_for_mutating_forms(self, tmp_path):
        """Every form that would REWRITE the hints must do nothing, quietly."""
        ctx, _ = _ctx_env(tmp_path, "openbsd")
        shim = ctx.build_dir / ".cvcpkg-shims" / "ldconfig"
        for args in (
            [],  # bare ldconfig -- rebuilds hints from /usr/lib alone
            ["-m", "/some/job/install/lib"],  # libtool finish_cmds
            ["/some/job/install/lib"],  # non-merging rebuild
            ["-U"],  # unconfigure
        ):
            p = subprocess.run([str(shim), *args], capture_output=True, text=True)
            assert p.returncode == 0, f"shim failed for {args}: {p.stderr}"
            assert p.stdout == "", f"shim spoke for {args}: {p.stdout!r}"

    def test_shim_is_executable(self, tmp_path):
        ctx, _ = _ctx_env(tmp_path, "openbsd")
        shim = ctx.build_dir / ".cvcpkg-shims" / "ldconfig"
        assert os.access(shim, os.X_OK)

    def test_no_shim_written_into_a_prefix(self, tmp_path):
        """Cache-restore contexts reuse the prefix as build_dir.

        No build script runs for those, and a shim written there would be
        packaged into the deliverable.
        """
        prefix = tmp_path / "prefix"
        ctx, _ = _ctx_env(tmp_path, "openbsd", build_dir=prefix, prefix=prefix)
        assert not (prefix / ".cvcpkg-shims").exists()
