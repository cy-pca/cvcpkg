"""_build_env points a POSIX build's TMPDIR into the build's own work dir.

In the system /tmp, what killed or crashed builds left behind accumulated until
/tmp filled, and then every job on that builder failed in its first second
("[Errno 28] No space left on device", openbsd-build-2, 2026-10-19).  Inside
the work dir, it is deleted with the build and lands on the volume the builder
measures and sweeps.

A unix socket under TMPDIR has to fit sockaddr_un.sun_path: 108 bytes on Linux,
104 on the BSDs.  The fleet's <work_dir>/tmp was 79 bytes for python312, and
Python's forkserver binds at TMPDIR + 32, so compileall -j0 failed and the
python312/313/313t bundles shipped without stdlib .pyc.  The fleet builder now
gives each job a short TMPDIR beside its job root, and run_build warns when a
TMPDIR is too long.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from click.testing import CliRunner

from cvcpkg import builder
from cvcpkg.builder import BuildContext, Recipe, _build_env, _tmpdir_socket_warning
from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run

_RECIPE = {
    "schema_version": 1,
    "recipe": {"name": "tp", "upstream_version": "1.0.0", "cvc_revision": 1},
    "source": {"type": "tarball", "url": "file:///x.tgz", "sha256": "0" * 64},
    "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
}

# The fleet's TMPDIR before this change: <work_root>/<job root>/<work dir>/tmp.
_OLD_FLEET_TMPDIR = (
    "/tmp/cvcpkg-builder/cvcpkg-job-python312-k3x9q2wa/cvcpkg-python312-m4b8z1rt/tmp"
)


def _ctx_for(tmp_path, tmp_dir=None):
    d = tmp_path / "recipes" / "tp"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(_RECIPE))
    (d / "build.sh").write_text("#!/bin/sh\n")
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
        tmp_dir=tmp_dir,
    )
    return ctx, work


def _env_for(tmp_path, tmp_dir=None):
    ctx, work = _ctx_for(tmp_path, tmp_dir)
    return _build_env(ctx, ctx.recipe.build_matrix[0]), work


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

    def test_a_callers_tmp_dir_replaces_the_work_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        short = tmp_path / "cvcpkg-t-abcdefgh"
        env, work = _env_for(tmp_path, tmp_dir=short)
        assert env["TMPDIR"] == str(short.resolve())
        assert short.is_dir()
        assert not (work / "tmp").exists()

    def test_windows_ignores_a_callers_tmp_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("TMPDIR", "/somewhere/else")
        env, _work = _env_for(tmp_path, tmp_dir=tmp_path / "cvcpkg-t-abcdefgh")
        assert env["TMPDIR"] == "/somewhere/else"


class TestSocketWarning:
    @pytest.mark.parametrize(
        ("platform", "size", "warns"),
        [
            # Python refuses a path that leaves no room for the NUL, so the
            # longest usable TMPDIR is sun_path - 33.
            ("linux", 75, False),
            ("linux", 76, True),
            ("freebsd14", 71, False),
            ("freebsd14", 72, True),
            ("netbsd10", 72, True),
            ("openbsd7", 72, True),
            ("darwin", 72, True),
        ],
    )
    def test_the_bound_is_sun_path_less_the_forkserver_tail(self, platform, size, warns):
        tmpdir = "/" + "x" * (size - 1)
        assert bool(_tmpdir_socket_warning(tmpdir, platform)) is warns

    def test_the_old_fleet_tmpdir_is_too_long_everywhere(self):
        assert len(_OLD_FLEET_TMPDIR) == 79
        for platform in ("linux", "freebsd14", "netbsd10", "openbsd7"):
            warning = _tmpdir_socket_warning(_OLD_FLEET_TMPDIR, platform)
            assert "TMPDIR is 79 bytes, too long for a unix socket" in warning
            assert _OLD_FLEET_TMPDIR in warning

    @pytest.mark.parametrize(
        "work_root",
        [
            "/tmp/cvcpkg-builder",
            "/tmp/cvcpkg-builder/cvcpkg-org",
            "/var/lib/cvcpkg-builder/cvcpkg-org",
        ],
    )
    def test_a_fleet_job_tmpdir_fits_everywhere(self, work_root):
        tmpdir = f"{work_root}/cvcpkg-t-abcdefgh"
        for platform in ("linux", "freebsd14", "netbsd10", "openbsd7"):
            assert _tmpdir_socket_warning(tmpdir, platform) == ""

    @pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="POSIX sockets")
    def test_the_bound_matches_this_hosts_sun_path(self, tmp_path, monkeypatch):
        # The constant is checked against a real bind: a relative name of
        # sun_path - 1 bytes binds, one more byte is refused.
        monkeypatch.chdir(tmp_path)
        limit = builder._sun_path_size()
        with socket.socket(socket.AF_UNIX) as s:
            s.bind("s" * (limit - 1))
        with socket.socket(socket.AF_UNIX) as s, pytest.raises(OSError):
            s.bind("t" * limit)


class TestRunBuildWarns:
    def test_a_tmpdir_too_long_for_a_socket_is_logged(self, tmp_path, monkeypatch):
        ctx, _work = _ctx_for(tmp_path, tmp_dir=tmp_path / ("t" * 80))

        class _Proc:
            stdout = iter([b"building\n"])

            def wait(self):
                return 0

        monkeypatch.setattr(builder, "sets_build_tmpdir", lambda: True)
        monkeypatch.setattr(builder, "_find_bash", lambda: "sh")
        monkeypatch.setattr(builder.proctree, "popen", lambda *a, **k: _Proc())
        monkeypatch.setattr(builder, "_merge_libdata_pkgconfig", lambda *a: None)
        monkeypatch.setattr(builder, "_make_relocatable", lambda *a: None)
        logged: list[str] = []
        builder.run_build(ctx, log_callback=logged.append)
        text = "".join(logged)
        assert "too long for a unix socket under it" in text, text
        assert str((tmp_path / ("t" * 80)).resolve()) in text


# ── The fleet builder's per-job TMPDIR ──────────────────────────


@pytest.fixture
def _quiet_builder(monkeypatch):
    """builder_run installs process-wide signal handlers and waits to drain."""
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")


def _run_job(monkeypatch, tmp_path, *, posix=True, pack_fails=False):
    """Run one job of ``app`` (with a locally built dep) through ``builder run``
    against a faked server, the harness of test_builder_local_only_deps.py, and
    report what the dep build and the pack were given."""
    from tests.unit.test_builder_local_only_deps import _fake_server, _recipe

    job = {"id": 42, "recipe_name": "app", "platform": "linux", "arch": "x86_64"}
    recipes = {
        "app": _recipe("app", ["vendorlib"]),
        "vendorlib": _recipe("vendorlib", redistributable=False),
    }
    posted: dict[str, list[dict]] = {}
    monkeypatch.setattr(httpx, "Client", _fake_server(job, recipes, {}, posted))
    monkeypatch.setattr("cvcpkg.builder.sets_build_tmpdir", lambda: posix)
    work_root = tmp_path / "wd"
    seen: dict = {}

    def fake_build_recipe(recipe_dir, *, prefix, **k):
        seen["dep_tmp_dir"] = k.get("tmp_dir")

    def fake_pack(recipe_dir, *, output_dir, **k):
        tmp = k.get("tmp_dir")
        seen["tmp_dir"] = tmp
        seen["tmp_was_dir"] = tmp is not None and tmp.is_dir()
        seen["job_root"] = k["work_dir_root"]
        seen["work_root_entries"] = sorted(p.name for p in work_root.iterdir())
        if pack_fails:
            raise RuntimeError("compile error")
        out = Path(output_dir) / "app-1.0.0-linux-x86_64.tar.gz"
        out.write_bytes(b"archive")
        return out, "0" * 64, out.stat().st_size

    monkeypatch.setattr("cvcpkg.builder.build_recipe", fake_build_recipe)
    monkeypatch.setattr("cvcpkg.builder.pack_recipe", fake_pack)
    monkeypatch.setattr(_builder, "_publish_to_server", lambda **k: None)
    args = [
        "--server",
        "http://test",
        "--token",
        "t",
        "--name",
        "w-job-tmpdir",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--no-websocket",
        "--no-auto-capabilities",
        "--no-free-disk",
        "--exit-when-empty",
        "--max-runtime",
        "60",
        "--work-dir",
        str(work_root),
        "--recipe-cache-dir",
        str(tmp_path / "rc"),
        "--pidfile",
        str(tmp_path / "b.pid"),
    ]
    result = CliRunner().invoke(builder_run, args)
    assert result.exit_code == 0, result.output
    return seen, posted, work_root, result.output


@pytest.mark.usefixtures("_quiet_builder")
class TestFleetJobTmpdir:
    def test_a_job_builds_with_a_short_tmpdir_beside_its_job_root(self, monkeypatch, tmp_path):
        seen, posted, work_root, output = _run_job(monkeypatch, tmp_path)
        assert posted.get("complete"), output
        tmp = seen["tmp_dir"]
        assert tmp is not None and seen["tmp_was_dir"], output
        # Directly in the work root, not inside the job root, so its length is
        # the work root's + 18 whatever the recipe is called.
        assert tmp.parent == work_root
        assert tmp.name.startswith("cvcpkg-t-")
        assert len(str(tmp)) == len(str(work_root)) + len("/cvcpkg-t-") + 8
        assert seen["job_root"].parent == work_root
        assert seen["job_root"] not in tmp.parents
        # Every build in the job shares it, the locally built dep included.
        assert seen["dep_tmp_dir"] == tmp
        # And it goes with the job.
        assert not tmp.exists()
        assert not seen["job_root"].exists()

    def test_a_failed_job_removes_its_tmpdir_too(self, monkeypatch, tmp_path):
        seen, posted, _work_root, output = _run_job(monkeypatch, tmp_path, pack_fails=True)
        assert posted.get("fail") and not posted.get("complete"), output
        assert seen["tmp_dir"] is not None
        assert not seen["tmp_dir"].exists()

    def test_a_windows_builder_makes_none(self, monkeypatch, tmp_path):
        seen, posted, _work_root, output = _run_job(monkeypatch, tmp_path, posix=False)
        assert posted.get("complete"), output
        assert seen["tmp_dir"] is None and seen["dep_tmp_dir"] is None
        assert not [n for n in seen["work_root_entries"] if n.startswith("cvcpkg-t-")]
