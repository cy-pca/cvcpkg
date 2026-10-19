"""_build_env points a POSIX build's TMPDIR into the build's own work dir.

In the system /tmp, what killed or crashed builds left behind accumulated until
/tmp filled, and then every job on that builder failed in its first second
("[Errno 28] No space left on device", openbsd-build-2, 2026-10-19).  Inside
the work dir, it is deleted with the build and lands on the volume the builder
measures and sweeps.
"""

from __future__ import annotations

import sys

import yaml

from cvcpkg.builder import BuildContext, Recipe, _build_env

_RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
    "source": {"type": "tarball", "url": "file:///x.tgz", "sha256": "0" * 64},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
}


def _env_for(tmp_path):
    d = tmp_path / "recipes" / "tp"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(_RECIPE))
    r = Recipe.load(d)
    work = tmp_path / "work"
    ctx = BuildContext(
        recipe=r,
        platform="linux",
        config="release",
        link="shared",
        prefix=tmp_path / "prefix",
        source_dir=work / "src",
        build_dir=work / "build",
        install_dir=work / "install",
        work_dir=work,
    )
    return _build_env(ctx, r.build_matrix[0]), work


class TestBuildEnvTmpdir:
    def test_posix_tmpdir_is_inside_the_work_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "openbsd7")
        monkeypatch.setenv("TMPDIR", "/tmp")
        env, work = _env_for(tmp_path)
        assert env["TMPDIR"] == str((work / "tmp").resolve())
        assert (work / "tmp").is_dir()

    def test_windows_keeps_the_callers_temp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TMPDIR", "/somewhere/else")
        env, work = _env_for(tmp_path)
        assert env["TMPDIR"] == "/somewhere/else"
        assert not (work / "tmp").exists()
