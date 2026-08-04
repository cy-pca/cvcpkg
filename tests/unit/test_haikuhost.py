"""Linux -> Haiku host build delegation (``cvcpkg.haikuhost``).

The SSH boundary is faked so the tests run anywhere (no Haiku box, no
network): the "remote" filesystem is a directory under tmp_path and the
push/pull/exec primitives copy into it.  What is exercised is the
decision logic, the job staging (remote path construction, dep prefix
rewriting, generated runner + env), the install-tree sync-back, remote
cleanup policy, command construction/quoting, and the ``run_build`` hook.
"""

import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cvcpkg import haikuhost
from cvcpkg.builder import BuildContext, MatrixEntry, PythonSpec, Recipe, SourceSpec

_REPO_ROOT = Path(__file__).resolve().parents[2]

# env-haiku.sh is a bash build-time helper (``[[ … ]]``, so /bin/sh will not do)
# that only ever runs on a Haiku host.  Windows has no usable bash — `bash` on
# the GitHub runner is the WSL launcher, which exits 1 with a UTF-16 "no
# installed distributions" banner — and windows recipes use build.ps1 and never
# source it.  Same guard as tests/unit/test_rewrite_deps_prefix.py.
_NEEDS_BASH = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX-only build-time helper (bash); windows recipes use build.ps1",
)


def _extract_block(text: str, start: str, end: str) -> str:
    """Lines from the one starting with *start* through the one with *end*."""
    lines = text.splitlines(keepends=True)
    first = next(i for i, ln in enumerate(lines) if ln.startswith(start))
    last = next(i for i, ln in enumerate(lines) if ln.startswith(end))
    return "".join(lines[first : last + 1])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every knob unset by default; the remote-probe cache starts empty."""
    for var in (
        "CVCPKG_HAIKUHOST",
        "CVCPKG_HAIKU_SSH",
        "CVCPKG_HAIKU_SSH_KEY",
        "CVCPKG_HAIKU_SSH_PORT",
        "CVCPKG_HAIKU_WORKDIR",
        "CVCPKG_HAIKU_TRANSFER",
        "CVCPKG_HAIKU_JOBS",
        "CVCPKG_HAIKU_KEEP_JOBS",
        "CVCPKG_HAIKU_JOB_TTL",
    ):
        monkeypatch.delenv(var, raising=False)
    haikuhost._remote_tool_cache.clear()
    haikuhost._own_jobs.clear()
    yield
    haikuhost._remote_tool_cache.clear()
    haikuhost._own_jobs.clear()


# ── should_delegate ─────────────────────────────────────────────


class TestShouldDelegate:
    def _configured(self, monkeypatch, *, on_haiku=False):
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: on_haiku)

    def test_delegates_haiku_cross_from_linux(self, monkeypatch):
        self._configured(monkeypatch)
        assert haikuhost.should_delegate("haiku", "linux") is True

    def test_delegates_without_explicit_host_platform(self, monkeypatch):
        """A haiku target from a non-haiku host is ALWAYS a cross build —
        unlike windows, it cannot be a native builder that just did not say
        so, so an empty host_platform must still delegate."""
        self._configured(monkeypatch)
        assert haikuhost.should_delegate("haiku", "") is True

    def test_declared_native_still_delegates_off_haiku(self, monkeypatch):
        """``host_platform: haiku`` cannot make a Linux toolchain emit Haiku
        binaries — it would only relabel Linux ones — so the declaration does
        NOT opt out while this process is not on Haiku."""
        self._configured(monkeypatch)
        assert haikuhost.should_delegate("haiku", "haiku") is True

    def test_on_haiku_not_delegated(self, monkeypatch):
        self._configured(monkeypatch, on_haiku=True)
        assert haikuhost.should_delegate("haiku", "linux") is False
        assert haikuhost.should_delegate("haiku", "haiku") is False

    def test_non_haiku_target_not_delegated(self, monkeypatch):
        self._configured(monkeypatch)
        assert haikuhost.should_delegate("linux", "linux") is False
        assert haikuhost.should_delegate("windows", "linux") is False

    def test_unconfigured_host_still_must_delegate(self, monkeypatch):
        """The supply-chain bug: this used to answer False on a builder with no
        CVCPKG_HAIKU_SSH, and the caller then ran the recipe's haiku build.sh
        through the LOCAL toolchain and packaged Linux binaries as
        haiku/x86_64.  Whether the job MUST be delegated is a property of the
        job, not of the builder's configuration."""
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        assert haikuhost.should_delegate("haiku", "linux") is True

    def test_env_kill_switch_does_not_enable_a_local_build(self, monkeypatch):
        self._configured(monkeypatch)
        monkeypatch.setenv("CVCPKG_HAIKUHOST", "0")
        assert haikuhost.should_delegate("haiku", "linux") is True

    def test_is_haiku_matches_every_sys_platform_spelling(self, monkeypatch):
        # Haiku reports "haiku", "haiku1" or "haikuR1~beta5" depending on the
        # interpreter — the prefix test has to catch all three.
        for spelling in ("haiku", "haiku1", "haikuR1~beta5"):
            monkeypatch.setattr(haikuhost.sys, "platform", spelling)
            assert haikuhost.is_haiku() is True
        monkeypatch.setattr(haikuhost.sys, "platform", "linux")
        assert haikuhost.is_haiku() is False


# ── ensure_delegatable ──────────────────────────────────────────


class TestEnsureDelegatable:
    """An un-delegatable haiku job must be a loud error, never a local build."""

    def test_unconfigured_builder_is_a_hard_error(self, monkeypatch):
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        with pytest.raises(haikuhost.HaikuhostError) as excinfo:
            haikuhost.ensure_delegatable("haiku", "linux")
        msg = str(excinfo.value)
        assert "CVCPKG_HAIKU_SSH" in msg  # names the knob that is missing
        assert "haiku/x86_64" in msg  # ...and what silence would have shipped

    def test_error_is_a_cvcpkg_error(self, monkeypatch):
        from cvcpkg.errors import CvcpkgError

        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        with pytest.raises(CvcpkgError):
            haikuhost.ensure_delegatable("haiku", "")

    def test_kill_switch_refuses_rather_than_building_locally(self, monkeypatch):
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setenv("CVCPKG_HAIKUHOST", "0")
        with pytest.raises(haikuhost.HaikuhostError, match="CVCPKG_HAIKUHOST=0"):
            haikuhost.ensure_delegatable("haiku", "linux")

    def test_configured_builder_passes(self, monkeypatch):
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        haikuhost.ensure_delegatable("haiku", "linux")  # must not raise

    def test_non_haiku_job_is_none_of_our_business(self, monkeypatch):
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        haikuhost.ensure_delegatable("linux", "linux")  # must not raise


# ── command construction ────────────────────────────────────────


class TestSshCommand:
    def test_batchmode_and_no_tty(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        cmd = haikuhost._ssh_cmd("true")
        assert cmd[0] == "ssh"
        assert "BatchMode=yes" in cmd
        assert "-T" in cmd
        assert cmd[-2:] == ["user@haiku-build", "true"]

    def test_key_and_port_options(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setenv("CVCPKG_HAIKU_SSH_KEY", "/keys/haiku_ed25519")
        monkeypatch.setenv("CVCPKG_HAIKU_SSH_PORT", "2222")
        cmd = haikuhost._ssh_cmd("true")
        assert cmd[cmd.index("-i") + 1] == "/keys/haiku_ed25519"
        assert cmd[cmd.index("-p") + 1] == "2222"

    def test_unconfigured_target_is_actionable(self):
        with pytest.raises(haikuhost.HaikuhostError, match="CVCPKG_HAIKU_SSH"):
            haikuhost._ssh_cmd("true")

    def test_job_name_sanitized(self):
        # A recipe name is schema-constrained, but a remote path is the wrong
        # place to rely on that.
        assert haikuhost._safe_name("zlib") == "zlib"
        assert haikuhost._safe_name("foo; rm -rf /") == "foo-rm--rf"
        assert haikuhost._safe_name("...") == "recipe"


class TestTransferMode:
    def test_auto_prefers_rsync_when_both_ends_have_it(self, monkeypatch):
        monkeypatch.setattr(haikuhost.shutil, "which", lambda t: "/usr/bin/rsync")
        monkeypatch.setattr(haikuhost, "_remote_has", lambda t: True)
        assert haikuhost._transfer_mode() == "rsync"

    def test_auto_falls_back_to_tar_without_remote_rsync(self, monkeypatch):
        # Haiku's base system has no rsync; tar-over-ssh needs nothing extra.
        monkeypatch.setattr(haikuhost.shutil, "which", lambda t: "/usr/bin/rsync")
        monkeypatch.setattr(haikuhost, "_remote_has", lambda t: False)
        assert haikuhost._transfer_mode() == "tar"

    def test_auto_falls_back_to_tar_without_local_rsync(self, monkeypatch):
        monkeypatch.setattr(haikuhost.shutil, "which", lambda t: None)
        monkeypatch.setattr(haikuhost, "_remote_has", lambda t: True)
        assert haikuhost._transfer_mode() == "tar"

    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_TRANSFER", "tar")
        assert haikuhost._transfer_mode() == "tar"

    def test_invalid_override_rejected(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_TRANSFER", "scp")
        with pytest.raises(haikuhost.HaikuhostError, match="invalid CVCPKG_HAIKU_TRANSFER"):
            haikuhost._transfer_mode()

    def test_remote_probe_is_cached(self, monkeypatch):
        calls: list[str] = []

        def _fake(cmd, **kw):
            calls.append(cmd)
            return ""

        monkeypatch.setattr(haikuhost, "_run_ssh", _fake)
        assert haikuhost._remote_has("rsync") is True
        assert haikuhost._remote_has("rsync") is True
        assert len(calls) == 1


# ── helpers to fabricate a build context ────────────────────────


def _make_recipe(tmp_path: Path, name="fakelib") -> Recipe:
    recipes_root = tmp_path / "recipes"
    rdir = recipes_root / name
    rdir.mkdir(parents=True)
    (rdir / "recipe.yaml").write_text(f"recipe: {{name: {name}}}\n")
    (rdir / "build.sh").write_text("# fake haiku build script\n")
    common = recipes_root / "_common"
    common.mkdir()
    (common / "env-haiku.sh").write_text("# fake env\n")
    return Recipe(
        name=name,
        upstream_version="1.0.0",
        cvc_revision=1,
        source=SourceSpec(type="tarball", url="https://example.invalid/src.tar.gz"),
        patches=[],
        build_matrix=[MatrixEntry(platform="haiku", script="build.sh")],
        package_files=["lib/*"],
        test_script=None,
        raw={"recipe": {"name": name}},
        recipe_dir=rdir,
    )


def _make_ctx(tmp_path: Path, recipe: Recipe) -> BuildContext:
    work = tmp_path / "work"
    src = work / "source"
    src.mkdir(parents=True)
    (src / "hello.c").write_text("int main(void){return 0;}\n")
    (src / "sub").mkdir()
    (src / "sub" / "x.h").write_text("#define X 1\n")
    deps = work / "deps-prefix"
    (deps / "lib" / "pkgconfig").mkdir(parents=True)
    (deps / "lib" / "pkgconfig" / "zlib.pc").write_text(f"prefix={deps}\nlibdir=${{prefix}}/lib\n")
    (deps / "bin").mkdir()
    return BuildContext(
        recipe=recipe,
        platform="haiku",
        config="release",
        link="shared",
        prefix=deps,
        source_dir=src,
        build_dir=work / "build",
        install_dir=work / "install",
        work_dir=work,
        host_platform="linux",
    )


class _FakeHaiku:
    """Fakes the SSH boundary against a local 'remote' filesystem.

    Remote absolute paths map 1:1 onto ``tmp_path/haiku/<path>``, so the
    module's remote-path arithmetic is exercised for real; the runner is
    not executed, only parsed (its exports are the contract we assert on).
    """

    def __init__(self, tmp_path: Path, monkeypatch, run_result=0):
        self.root = tmp_path / "haiku"
        self.root.mkdir()
        self.run_result = run_result
        self.ssh_calls: list[str] = []
        self.pushes: list[tuple[Path, str]] = []
        self.installed_files = ["lib/libfake.so", "include/fake.h"]
        self.transfer = "tar"

        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setattr(haikuhost, "_transfer_mode", lambda: self.transfer)
        monkeypatch.setattr(haikuhost, "_run_ssh", self._run_ssh)
        monkeypatch.setattr(haikuhost, "_push_tree", self._push_tree)
        monkeypatch.setattr(haikuhost, "_pull_tree", self._pull_tree)
        monkeypatch.setattr(haikuhost, "_put_text", self._put_text)
        monkeypatch.setattr(haikuhost, "_stream_ssh", self._stream_ssh)

    # -- remote path mapping --

    def local(self, remote: str) -> Path:
        assert remote.startswith("/"), remote
        return self.root / remote.lstrip("/")

    # -- faked primitives --

    def _run_ssh(self, command: str, *, what: str = "", timeout: float = 300) -> str:
        self.ssh_calls.append(command)
        words = shlex.split(command)
        if words[:2] == ["mkdir", "-p"]:
            for d in words[2:]:
                self.local(d).mkdir(parents=True, exist_ok=True)
        elif words[:2] == ["rm", "-rf"]:
            for d in words[2:]:
                shutil.rmtree(self.local(d), ignore_errors=True)
        elif words[:2] == ["rm", "-f"]:
            for f in words[2:]:
                self.local(f).unlink(missing_ok=True)
        return ""

    def _push_tree(self, local: Path, remote_dir: str, *, what: str) -> str:
        self.pushes.append((local, remote_dir))
        shutil.copytree(local, self.local(remote_dir), symlinks=True, dirs_exist_ok=True)
        return self.transfer

    def _pull_tree(self, remote_dir: str, local: Path, *, what: str) -> str:
        local.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.local(remote_dir), local, symlinks=True, dirs_exist_ok=True)
        return self.transfer

    def _put_text(self, text: str, remote_path: str, *, what: str) -> None:
        dst = self.local(remote_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text)

    def _stream_ssh(self, command: str, log_callback) -> int:
        self.ssh_calls.append(command)
        runner = self.local(shlex.split(command)[-1])
        env = runner_env(runner.read_text())
        if self.run_result == 0:
            for rel in self.installed_files:
                out = self.local(env["CVC_INSTALL_DIR"]) / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("fake")
        return self.run_result

    # -- inspection helpers --

    @property
    def job_dirs(self) -> list[Path]:
        return sorted((self.root / "boot/home/cvcpkg-build/jobs").iterdir())


def runner_env(script: str) -> dict[str, str]:
    """Parse the ``export NAME=value`` lines out of a generated runner."""
    env: dict[str, str] = {}
    for line in script.splitlines():
        if not line.startswith("export ") or "=" not in line:
            continue
        name, _, value = line[len("export ") :].partition("=")
        if not value:
            continue  # `export PATH` after a separate assignment
        env[name] = shlex.split(value)[0] if value else ""
    return env


# ── job staging + sync-back ─────────────────────────────────────


class TestRunHaikuBuild:
    def test_stages_builds_and_syncs_back(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        logs: list[str] = []
        haikuhost.run_haiku_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh", logs.append
        )

        # Install tree came back to the Linux side for pack/publish.
        assert (ctx.install_dir / "lib" / "libfake.so").is_file()
        assert (ctx.install_dir / "include" / "fake.h").is_file()
        # The transfer mode is named in the log (tar vs rsync matters on Haiku).
        assert any("via tar" in line for line in logs)
        # Remote work dir removed on success.
        assert host.job_dirs == []

    def test_remote_layout_and_runner_env(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True  # keep the remote job dir for inspection

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        (job_dir,) = host.job_dirs
        assert job_dir.name.startswith("fakelib-")

        # Staged trees, with _common as a SIBLING of the recipe dir (build.sh
        # sources "$0/../../_common/env-haiku.sh").
        assert (job_dir / "source" / "hello.c").is_file()
        assert (job_dir / "source" / "sub" / "x.h").is_file()
        assert (job_dir / "recipe" / recipe.name / "build.sh").is_file()
        assert (job_dir / "recipe" / "_common" / "env-haiku.sh").is_file()
        assert (job_dir / "deps" / "bin").is_dir()

        env = runner_env((job_dir / "run-job.sh").read_text())
        remote_job = f"/boot/home/cvcpkg-build/jobs/{job_dir.name}"
        assert env["CVC_SOURCE_DIR"] == remote_job + "/source"
        assert env["CVC_BUILD_DIR"] == remote_job + "/build"
        assert env["CVC_INSTALL_DIR"] == remote_job + "/install"
        assert env["CVC_DEPS_PREFIX"] == remote_job + "/deps"
        assert env["CVC_RECIPE_DIR"] == remote_job + "/recipe/" + recipe.name
        assert env["CVC_PLATFORM"] == "haiku"
        assert env["CVC_HAIKUHOST"] == "1"
        assert env["CVC_COMPONENT"] == recipe.name
        assert env["CMAKE_BUILD_TYPE"] == "Release"
        assert env["BUILD_SHARED_LIBS"] == "ON"
        # No build-prefix separation here -> CVC_BUILD_PREFIX falls back to
        # deps so remote scripts always have a valid root to resolve against.
        assert env["CVC_BUILD_PREFIX"] == remote_job + "/deps"

    def test_runner_uses_haiku_library_path(self, tmp_path, monkeypatch):
        """Haiku's runtime_loader reads LIBRARY_PATH; LD_LIBRARY_PATH is
        ignored outright, and setting the wrong one fails silently."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        (job_dir,) = host.job_dirs
        runner = (job_dir / "run-job.sh").read_text()
        assert "export LIBRARY_PATH" in runner
        assert "LD_LIBRARY_PATH" not in runner
        # The recipe script is handed to bash: /bin/sh being bash is a Haiku
        # convention, not a contract, and env-haiku.sh is bash-only.
        assert 'exec bash "$_script"' in runner

    def test_build_prefix_is_staged_and_mapped(self, tmp_path, monkeypatch):
        """A separated build prefix (host tools / staged source packages) must
        reach the Haiku side and be exposed as CVC_BUILD_PREFIX — otherwise a
        recipe consuming a source package finds nothing there."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        bp = tmp_path / "prefix.build"
        (bp / "src" / "mathsrc").mkdir(parents=True)
        (bp / "src" / "mathsrc" / "addmul.c").write_text("int f(void){return 1;}\n")
        (bp / "bin").mkdir(parents=True)
        (bp / "bin" / "sometool").write_text("#!/bin/sh\n")
        ctx.build_prefix = bp

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        (job_dir,) = host.job_dirs
        assert (job_dir / "build-prefix" / "src" / "mathsrc" / "addmul.c").is_file()
        assert (job_dir / "build-prefix" / "bin" / "sometool").is_file()

        env = runner_env((job_dir / "run-job.sh").read_text())
        remote_job = f"/boot/home/cvcpkg-build/jobs/{job_dir.name}"
        assert env["CVC_BUILD_PREFIX"] == remote_job + "/build-prefix"
        assert env["CVC_BUILD_PREFIX"] != env["CVC_DEPS_PREFIX"]

    def test_dep_metadata_rewritten_to_remote_paths(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        (job_dir,) = host.job_dirs
        pc = (job_dir / "deps" / "lib" / "pkgconfig" / "zlib.pc").read_text()
        assert str(ctx.prefix) not in pc
        assert f"/boot/home/cvcpkg-build/jobs/{job_dir.name}/deps" in pc
        # The local prefix is left untouched — other builds still use it.
        assert str(ctx.prefix) in (ctx.prefix / "lib" / "pkgconfig" / "zlib.pc").read_text()
        # ...and only the rewritten files were staged, not a copy of the prefix.
        stage_pushes = [p for p, _ in host.pushes if ".haikuhost-stage-" in str(p)]
        assert stage_pushes and not any((p / "bin").exists() for p in stage_pushes)

    def test_stage_dir_removed_from_the_local_work_dir(self, tmp_path, monkeypatch):
        _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        assert not list(ctx.work_dir.glob(".haikuhost-stage-*"))

    def test_matrix_env_overrides(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.build_matrix[0].env = {"CFLAGS": "-O2", "CVC_LINK": "static"}
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        (job_dir,) = host.job_dirs
        env = runner_env((job_dir / "run-job.sh").read_text())
        assert env["CFLAGS"] == "-O2"
        assert env["CVC_LINK"] == "static"  # matrix env wins

    def test_jobs_override(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_HAIKU_JOBS", "3")
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        (job_dir,) = host.job_dirs
        assert runner_env((job_dir / "run-job.sh").read_text())["CVC_JOBS"] == "3"

    def test_non_posix_env_name_not_emitted(self, tmp_path, monkeypatch):
        """A matrix entry can carry anything; a bogus name must be dropped,
        never spliced into the runner as a bare line."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.build_matrix[0].env = {"bad name; rm -rf /": "x", "GOOD": "y"}
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        runner = (host.job_dirs[0] / "run-job.sh").read_text()
        assert "rm -rf /" not in runner
        assert "export GOOD=y" in runner

    def test_env_values_are_quoted(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.build_matrix[0].env = {"CFLAGS": "-O2 -DX=$(id -u)"}
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        runner = (host.job_dirs[0] / "run-job.sh").read_text()
        assert "export CFLAGS='-O2 -DX=$(id -u)'" in runner

    def test_remote_failure_keeps_the_work_dir(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch, run_result=7)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        with pytest.raises(haikuhost.HaikuhostError, match="exit code 7") as excinfo:
            haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        # The error names the wreckage so it can be inspected...
        assert "user@haiku-build:/boot/home/cvcpkg-build/jobs/" in str(excinfo.value)
        # ...and it really is still there.
        assert len(host.job_dirs) == 1
        assert not any(c.startswith("rm -rf") for c in host.ssh_calls)

    def test_empty_install_tree_fails(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        host.installed_files = []
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        with pytest.raises(haikuhost.HaikuhostError, match="installed no files"):
            haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        assert len(host.job_dirs) == 1

    def test_custom_workdir(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_HAIKU_WORKDIR", "/boot/home/elsewhere/")
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        assert (host.root / "boot/home/elsewhere/jobs").is_dir()

    def test_remote_paths_reach_the_shell_quoted(self, tmp_path, monkeypatch):
        """Nothing is interpolated into a remote command unquoted: a work dir
        with a space must survive the remote shell as ONE word."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_HAIKU_WORKDIR", "/boot/home/cvc build")
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        mkdir = next(c for c in host.ssh_calls if c.startswith("mkdir -p "))
        assert "'/boot/home/cvc build/jobs/" in mkdir
        # Every word the remote shell would see is a real path, not a fragment.
        assert all(w.startswith("/boot/home/cvc build/") for w in shlex.split(mkdir)[2:])
        rm = next(c for c in host.ssh_calls if c.startswith("rm -rf "))
        assert shlex.split(rm)[2].startswith("/boot/home/cvc build/jobs/")

    def test_non_sh_script_rejected(self, tmp_path, monkeypatch):
        _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.build_matrix[0].script = "build.ps1"
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(haikuhost.HaikuhostError, match="only supports .sh"):
            haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1")

    def test_unconfigured_host_is_actionable(self, tmp_path, monkeypatch):
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(haikuhost.HaikuhostError, match="CVCPKG_HAIKU_SSH"):
            haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

    def test_python_recipe_env_is_mirrored(self, tmp_path, monkeypatch):
        """_job_env claims to mirror _build_env; the CVC_PYTHON_* trio decides
        which interpreter a wheel installs into, so dropping it built every
        python column against whatever python3 the box happened to have."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path, name="numpy-cp313t")
        recipe.python = PythonSpec(
            interpreter="python313t", abi="cp313t", manylinux_min="manylinux_2_28"
        )
        recipe.build_matrix[0].host_platform = "linux"
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        env = runner_env((host.job_dirs[0] / "run-job.sh").read_text())
        assert env["CVC_PYTHON_INTERPRETER"] == "python313t"
        assert env["CVC_PYTHON_ABI"] == "cp313t"
        assert env["CVC_PYTHON_MANYLINUX_MIN"] == "manylinux_2_28"
        assert env["PYTHON_GIL"] == "0"  # free-threaded ABI: never re-enable it
        assert env["CVC_HOST_PLATFORM"] == "linux"

    def test_cross_toolchain_env_is_mirrored(self, tmp_path, monkeypatch):
        """_build_env and haikuhost's own _test_env both expand
        cross_toolchain.env against the prefix; _job_env silently dropped it,
        so a recipe that resolves a toolchain out of the build closure got
        nothing on the Haiku side."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.cross_toolchain_env = {"CVC_FAKE_SDK_DIR": "${PREFIX}/opt/fake-sdk"}
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        env = runner_env((host.job_dirs[0] / "run-job.sh").read_text())
        # Expanded against the REMOTE build prefix, never the Linux-side path.
        assert env["CVC_FAKE_SDK_DIR"] == env["CVC_BUILD_PREFIX"] + "/opt/fake-sdk"
        assert str(tmp_path) not in env["CVC_FAKE_SDK_DIR"]

    def test_cross_toolchain_env_follows_a_separated_build_prefix(self, tmp_path, monkeypatch):
        """_build_env expands ${PREFIX} against the BUILD prefix (that is where
        toolchains are installed), so the remote form must do the same."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        bp = tmp_path / "prefix.build"
        (bp / "bin").mkdir(parents=True)
        ctx.build_prefix = bp
        ctx.cross_toolchain_env = {"CVC_FAKE_SDK_DIR": "${PREFIX}/opt/fake-sdk"}
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        env = runner_env((host.job_dirs[0] / "run-job.sh").read_text())
        assert env["CVC_BUILD_PREFIX"].endswith("/build-prefix")
        assert env["CVC_FAKE_SDK_DIR"] == env["CVC_BUILD_PREFIX"] + "/opt/fake-sdk"

    def test_the_install_tree_is_cleared_before_the_pull(self, tmp_path, monkeypatch):
        """The pull MERGES into the destination (no --delete, tar -x over an
        existing dir), so a stale local file would be packed into the bundle as
        if the Haiku build had produced it."""
        _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.install_dir.mkdir(parents=True, exist_ok=True)
        (ctx.install_dir / "lib").mkdir()
        (ctx.install_dir / "lib" / "libstale.so").write_text("from an earlier run")

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        assert (ctx.install_dir / "lib" / "libfake.so").is_file()
        assert not (ctx.install_dir / "lib" / "libstale.so").exists()

    def test_a_shared_prefix_is_never_wiped(self, tmp_path, monkeypatch):
        """The clear is guarded: every path into run_build hands us this
        recipe's own isolated install dir, but if one ever handed us the shared
        prefix instead, wiping it would delete every dependency built so far —
        a much worse outcome than the stale file it is there to prevent."""
        _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.install_dir = Path(ctx.prefix)  # the pathological caller
        (ctx.install_dir / "lib" / "libdep.so").write_text("an earlier recipe")

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        assert (ctx.install_dir / "lib" / "libdep.so").is_file()

    def test_non_python_recipe_gets_no_python_env(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        env = runner_env((host.job_dirs[0] / "run-job.sh").read_text())
        assert "CVC_PYTHON_ABI" not in env
        assert "PYTHON_GIL" not in env
        assert "CVC_HOST_PLATFORM" not in env


class TestLibraryPathDefaults:
    """LIBRARY_PATH must be built from Haiku's defaults, not from inheritance.

    An ``ssh host 'sh run-job.sh'`` exec channel gets none of the login
    environment, so ``${LIBRARY_PATH:+:$LIBRARY_PATH}`` appended nothing and
    the exported value named ONLY our prefixes — every system library then
    unresolvable.
    """

    def _runner(self, tmp_path, monkeypatch) -> str:
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True
        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        return (host.job_dirs[0] / "run-job.sh").read_text()

    def test_system_defaults_are_spelled_out(self, tmp_path, monkeypatch):
        runner = self._runner(tmp_path, monkeypatch)
        for entry in haikuhost.HAIKU_DEFAULT_LIBRARY_PATH.split(":"):
            assert entry in runner, f"{entry} missing from the runner's LIBRARY_PATH"
        assert "/boot/system/lib" in runner

    def test_defaults_are_emitted_expandably(self, tmp_path, monkeypatch):
        """$HOME has to expand on the FAR end, so the default list may not be
        single-quoted; %A is expanded by runtime_loader, not the shell."""
        runner = self._runner(tmp_path, monkeypatch)
        assert '"%A/lib:$HOME/config/non-packaged/lib' in runner
        assert "'%A/lib" not in runner

    def test_prefixes_still_win_over_the_system(self, tmp_path, monkeypatch):
        runner = self._runner(tmp_path, monkeypatch)
        line = next(
            ln for ln in runner.splitlines() if ln.startswith("LIBRARY_PATH=") and "$CVC_" in ln
        )
        assert line.index("$CVC_BUILD_PREFIX/lib") < line.index("$CVC_DEPS_PREFIX/lib")
        assert line.endswith(':$_cvc_sys_libs"')

    def test_inherited_value_is_honoured_when_present(self, tmp_path, monkeypatch):
        """If the far end does export one, use it instead of our guess."""
        runner = self._runner(tmp_path, monkeypatch)
        assert 'if [ -n "${LIBRARY_PATH:-}" ]; then' in runner
        assert '_cvc_sys_libs="$LIBRARY_PATH"' in runner

    def test_runner_is_valid_posix_sh(self, tmp_path, monkeypatch):
        """The generated runner is executed by /bin/sh on a box we cannot
        test against, so at least parse it here."""
        script = tmp_path / "run-job.sh"
        script.write_text(self._runner(tmp_path, monkeypatch))
        assert subprocess.run(["sh", "-n", str(script)]).returncode == 0

    def test_env_haiku_sh_agrees_on_the_ordering(self):
        """The runner and env-haiku.sh both build LIBRARY_PATH, in that order,
        and must agree on the layering: cvcpkg's prefixes first, Haiku's own
        directories last (same rule as PATH and PKG_CONFIG_PATH, and the reason
        the prefix exists at all).  env-haiku.sh used to append the inherited
        value LAST, which put /boot/system/develop/lib ahead of the build
        prefix and the component's own install dir — the exact inversion.
        """
        text = (_REPO_ROOT / "recipes" / "_common" / "env-haiku.sh").read_text()
        export = next(ln for ln in text.splitlines() if ln.startswith("export LIBRARY_PATH="))
        assert export.index("_HAIKU_LIBS") < export.index("_HAIKU_SYS_LIBS")

    @_NEEDS_BASH
    def test_env_haiku_sh_layers_that_ordering_when_run(self, tmp_path):
        """The same ordering, behaviourally: run env-haiku.sh's layering with a
        runner-shaped LIBRARY_PATH and check nothing of Haiku's overtakes ours.
        Split from its textual sibling because only this half needs a shell."""
        text = (_REPO_ROOT / "recipes" / "_common" / "env-haiku.sh").read_text()
        runner_value = "/j/bp/lib:/j/deps/lib:/j/install/lib:/boot/system/lib"
        script = tmp_path / "layer.sh"
        script.write_text(
            "set -u\n"
            "CVC_DEPS_PREFIX=/j/deps\n"
            '_HAIKU_SYSTEM="/boot/system"\n'
            '_HAIKU_NONPKG="${_HAIKU_SYSTEM}/non-packaged"\n'
            + _extract_block(text, "_HAIKU_SYS_LIBS=", "export LIBRARY_PATH=")
            + 'echo "$LIBRARY_PATH"\n'
        )
        out = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            env={**os.environ, "LIBRARY_PATH": runner_value},
        )
        assert out.returncode == 0, out.stderr
        got = out.stdout.strip().split(":")
        assert got[:4] == runner_value.split(":")[:4]
        assert got[-2:] == ["/boot/system/develop/lib", "/boot/system/non-packaged/develop/lib"]
        assert got.count("/j/deps/lib") == 1  # the guard against duplication


class TestReapOldJobs:
    """Failed job dirs are kept for inspection; unbounded, they fill the box."""

    def _fake_ssh(self, monkeypatch, names, markers: dict | None = None):
        """Fake the listing.  *markers* maps a job name to its marker contents."""
        calls: list[str] = []
        markers = markers or {}

        def _run(command: str, *, what: str = "", timeout: float = 300) -> str:
            calls.append(command)
            if "ls -1t" in command:
                # The remote loop emits "<name>|<marker>", newest first.
                return "\n".join(f"{n}|{markers.get(n, '')}" for n in names)
            return ""

        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setattr(haikuhost, "_run_ssh", _run)
        return calls

    def _removed(self, calls: list[str]) -> list[str]:
        out: list[str] = []
        for c in calls:
            if c.startswith("rm -rf "):
                out += shlex.split(c)[2:]
        return out

    def test_keeps_the_newest_and_reaps_the_rest(self, monkeypatch):
        # ls -1t is newest-first, so the tail is the oldest.
        names = [f"fakelib-{i:012d}" for i in range(6)]
        calls = self._fake_ssh(monkeypatch, names)
        haikuhost._reap_old_jobs(lambda m: None)
        removed = self._removed(calls)
        assert removed == [f"/boot/home/cvcpkg-build/jobs/{n}" for n in names[3:]]

    def test_nothing_to_reap_issues_no_rm(self, monkeypatch):
        calls = self._fake_ssh(monkeypatch, ["fakelib-a", "fakelib-b"])
        haikuhost._reap_old_jobs(lambda m: None)
        assert not self._removed(calls)

    def test_keep_count_is_configurable(self, monkeypatch):
        names = [f"fakelib-{i}" for i in range(4)]
        calls = self._fake_ssh(monkeypatch, names)
        monkeypatch.setenv("CVCPKG_HAIKU_KEEP_JOBS", "1")
        haikuhost._reap_old_jobs(lambda m: None)
        assert self._removed(calls) == [f"/boot/home/cvcpkg-build/jobs/{n}" for n in names[1:]]

    def test_reaping_can_be_disabled(self, monkeypatch):
        calls = self._fake_ssh(monkeypatch, [f"fakelib-{i}" for i in range(9)])
        monkeypatch.setenv("CVCPKG_HAIKU_KEEP_JOBS", "0")
        haikuhost._reap_old_jobs(lambda m: None)
        assert not calls  # not even a listing

    def test_only_names_we_could_have_minted_are_removed(self, monkeypatch):
        """A remote listing is remote input; `rm -rf` is the wrong place to be
        relaxed about it."""
        names = ["ok-1", "ok-2", "ok-3", "..", "weird name", "-rf /boot", "ok-4"]
        calls = self._fake_ssh(monkeypatch, names)
        haikuhost._reap_old_jobs(lambda m: None)
        assert self._removed(calls) == ["/boot/home/cvcpkg-build/jobs/ok-4"]

    def test_listing_failure_is_not_fatal(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")

        def _boom(command, *, what="", timeout=300):
            raise haikuhost.HaikuhostError("ssh died")

        monkeypatch.setattr(haikuhost, "_run_ssh", _boom)
        logs: list[str] = []
        haikuhost._reap_old_jobs(logs.append)  # must not raise
        assert any("reap" in ln for ln in logs)

    def test_invalid_keep_count_is_not_fatal(self, monkeypatch):
        calls = self._fake_ssh(monkeypatch, ["fakelib-a"] * 9)
        monkeypatch.setenv("CVCPKG_HAIKU_KEEP_JOBS", "lots")
        logs: list[str] = []
        haikuhost._reap_old_jobs(logs.append)  # must not raise
        assert not calls
        assert any("CVCPKG_HAIKU_KEEP_JOBS" in ln for ln in logs)

    def test_a_build_reaps_before_it_stages(self, tmp_path, monkeypatch):
        """The reap has to happen BEFORE the new job dir exists, or the newest
        entry it keeps is the job that has not run yet."""
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")

        listings = [i for i, c in enumerate(host.ssh_calls) if "ls -1t" in c]
        mkdirs = [i for i, c in enumerate(host.ssh_calls) if c.startswith("mkdir -p")]
        assert listings and mkdirs and listings[0] < mkdirs[0]

    def test_listing_command_needs_only_ls_and_cat(self, monkeypatch):
        """A stock Haiku box has no guarantee of find/stat/head — the listing
        must get by on `ls` and `cat`, and it must parse as POSIX sh."""
        calls = self._fake_ssh(monkeypatch, [])
        haikuhost._reap_old_jobs(lambda m: None)
        (listing,) = (c for c in calls if "ls -1t" in c)
        for forbidden in ("find ", "stat ", "head ", "xargs "):
            assert forbidden not in listing
        assert subprocess.run(["sh", "-n", "-c", listing]).returncode == 0


class TestReaperLiveness:
    """The reaper must never delete a job dir that is in use.

    Count-based reaping alone ("delete everything past the newest N") is only
    safe while one builder owns the box.  Two Haiku jobs against one host — two
    builders sharing a CVCPKG_HAIKU_WORKDIR, or a re-drive launched beside a
    running one — and the newer job deletes the older one's source and build
    tree mid-compile.  ``ls -1t`` sorts by mtime and a long build barely touches
    its own top-level dir, so the longer a build runs the more likely it is to
    be the thing destroyed.
    """

    def _fake_ssh(self, monkeypatch, entries):
        """*entries* is a list of ``(name, marker)`` pairs, newest first."""
        calls: list[str] = []

        def _run(command: str, *, what: str = "", timeout: float = 300) -> str:
            calls.append(command)
            if "ls -1t" in command:
                return "\n".join(f"{n}|{m}" for n, m in entries)
            return ""

        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setattr(haikuhost, "_run_ssh", _run)
        return calls

    def _removed(self, calls: list[str]) -> list[str]:
        out: list[str] = []
        for c in calls:
            if c.startswith("rm -rf "):
                out += shlex.split(c)[2:]
        return out

    @pytest.fixture(autouse=True)
    def _no_stale_claims(self):
        haikuhost._own_jobs.clear()
        yield
        haikuhost._own_jobs.clear()

    def test_a_live_marker_survives_the_count_bound(self, monkeypatch):
        """The headline case: a concurrent build's dir is well past the newest
        3, and must still be there when that build finishes."""
        now = int(time.time())
        entries = [(f"fakelib-{i}", "") for i in range(5)]
        entries.append(("llvm-runningnow", f"{now - 3600} pid99@peer"))
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        removed = self._removed(calls)
        assert "/boot/home/cvcpkg-build/jobs/llvm-runningnow" not in removed
        # ...and the finished dirs past the keep count still go.
        assert removed == [f"/boot/home/cvcpkg-build/jobs/fakelib-{i}" for i in (3, 4)]

    def test_live_dirs_do_not_consume_the_keep_budget(self, monkeypatch):
        """`keep` counts FINISHED dirs: a running job is not "one of the newest
        three kept for inspection", it is not kept at all."""
        now = int(time.time())
        entries = [("running", f"{now} pid1@peer")] + [(f"old-{i}", "") for i in range(4)]
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert self._removed(calls) == ["/boot/home/cvcpkg-build/jobs/old-3"]

    def test_an_expired_marker_stops_protecting(self, monkeypatch):
        """A builder that is SIGKILLed leaves its marker behind; without an
        expiry that one dir would be unreapable forever — the leak the reaper
        exists to prevent, one level up."""
        stamp = int(time.time()) - haikuhost.DEFAULT_JOB_TTL - 60
        entries = [(f"fakelib-{i}", "") for i in range(3)] + [("crashed", f"{stamp} pid7@dead")]
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert self._removed(calls) == ["/boot/home/cvcpkg-build/jobs/crashed"]

    def test_ttl_is_configurable(self, monkeypatch):
        stamp = int(time.time()) - 7200
        entries = [(f"fakelib-{i}", "") for i in range(3)] + [("longbuild", f"{stamp} pid7@peer")]
        monkeypatch.setenv("CVCPKG_HAIKU_JOB_TTL", "3600")  # shorter than its age
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert self._removed(calls) == ["/boot/home/cvcpkg-build/jobs/longbuild"]

    def test_invalid_ttl_is_not_fatal(self, monkeypatch):
        calls = self._fake_ssh(monkeypatch, [(f"fakelib-{i}", "") for i in range(9)])
        monkeypatch.setenv("CVCPKG_HAIKU_JOB_TTL", "soon")
        logs: list[str] = []
        haikuhost._reap_old_jobs(logs.append)  # must not raise
        assert not calls
        assert any("CVCPKG_HAIKU_JOB_TTL" in ln for ln in logs)

    def test_a_future_stamp_counts_as_live(self, monkeypatch):
        """Clock skew between two builders must not read as "long dead"."""
        stamp = int(time.time()) + 3600
        entries = [(f"fakelib-{i}", "") for i in range(4)] + [("skewed", f"{stamp} pid7@peer")]
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert "/boot/home/cvcpkg-build/jobs/skewed" not in self._removed(calls)

    def test_an_unparsable_marker_counts_as_live(self, monkeypatch):
        """A marker we cannot read is still a marker: the safe reading of
        garbage is "someone is here", not "help yourself"."""
        entries = [(f"fakelib-{i}", "") for i in range(4)] + [("odd", "not-a-timestamp")]
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert "/boot/home/cvcpkg-build/jobs/odd" not in self._removed(calls)

    def test_this_process_never_reaps_its_own_job(self, monkeypatch):
        """Belt to the marker's braces: if the marker write failed, the local
        registration still has to keep our own in-flight dir alive."""
        entries = [(f"fakelib-{i}", "") for i in range(5)] + [("mine-abc", "")]
        haikuhost._own_jobs.add("mine-abc")
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert "/boot/home/cvcpkg-build/jobs/mine-abc" not in self._removed(calls)

    def test_a_finished_dir_of_ours_is_reapable_again(self, monkeypatch):
        """The claim is dropped when the job ends, or a long-lived builder
        daemon would accumulate kept dirs the count bound can never touch."""
        entries = [(f"fakelib-{i}", "") for i in range(5)] + [("mine-abc", "")]
        haikuhost._own_jobs.add("mine-abc")
        haikuhost._own_jobs.discard("mine-abc")  # what _finish_job does
        calls = self._fake_ssh(monkeypatch, entries)
        haikuhost._reap_old_jobs(lambda m: None)
        assert "/boot/home/cvcpkg-build/jobs/mine-abc" in self._removed(calls)


class TestJobClaims:
    """The marker's lifecycle, through a real (faked-SSH) build."""

    def _marker(self, job_dir: Path) -> Path:
        return job_dir / haikuhost.JOB_MARKER

    def test_a_running_job_is_marked_before_anything_is_staged(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        seen: list[str] = []

        real_push = host._push_tree

        def _push(local, remote_dir, *, what):
            # By the time the first byte is staged, the dir must be claimed.
            job_root = "/".join(remote_dir.split("/")[:6])
            seen.append(str(self._marker(host.local(job_root)).is_file()))
            return real_push(local, remote_dir, what=what)

        monkeypatch.setattr(haikuhost, "_push_tree", _push)
        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        assert seen and set(seen) == {"True"}

    def test_the_marker_records_when_and_who(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True
        text = haikuhost._marker_text()
        stamp, owner = text.split()
        assert abs(int(stamp) - time.time()) < 60
        assert str(os.getpid()) in owner
        assert len(text.splitlines()) == 1  # `cat` reads it back as one field pair
        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        assert host.job_dirs  # dir kept

    def test_a_kept_dir_stops_looking_live(self, tmp_path, monkeypatch):
        """--keep-build-dir / a failure keeps the dir for inspection, and the
        count bound must still be able to reach it later."""
        host = _FakeHaiku(tmp_path, monkeypatch, run_result=9)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(haikuhost.HaikuhostError):
            haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        (job_dir,) = host.job_dirs
        assert not self._marker(job_dir).exists()
        assert job_dir.name not in haikuhost._own_jobs

    def test_a_successful_job_releases_its_claim(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        haikuhost.run_haiku_build(ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh")
        assert host.job_dirs == []
        assert not haikuhost._own_jobs

    def test_a_failed_marker_write_does_not_fail_the_build(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        real_put = host._put_text

        def _put(text, remote_path, *, what):
            if remote_path.endswith(haikuhost.JOB_MARKER):
                raise haikuhost.HaikuhostError("read-only filesystem")
            return real_put(text, remote_path, what=what)

        monkeypatch.setattr(haikuhost, "_put_text", _put)
        logs: list[str] = []
        haikuhost.run_haiku_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh", logs.append
        )
        assert (ctx.install_dir / "lib" / "libfake.so").is_file()
        assert any("could not mark" in ln for ln in logs)

    def test_a_test_job_is_claimed_too(self, tmp_path, monkeypatch):
        host = _FakeHaiku(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.test_script = "test.sh"
        (recipe.recipe_dir / "test.sh").write_text("# fake\n")
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True
        (ctx.install_dir / "lib").mkdir(parents=True)
        (ctx.install_dir / "lib" / "libfake.so").write_text("fake")

        claimed: list[str] = []
        monkeypatch.setattr(
            haikuhost,
            "_claim_job",
            lambda job, name, log: claimed.append(name),
        )
        haikuhost.run_haiku_test(ctx, recipe.recipe_dir / "test.sh")
        assert claimed and "-test-" in claimed[0]
        assert host.job_dirs


class TestRunHaikuTest:
    """The test script must run where the binaries can actually run."""

    def _tested(self, tmp_path, monkeypatch, *, run_result=0):
        host = _FakeHaiku(tmp_path, monkeypatch, run_result=run_result)
        recipe = _make_recipe(tmp_path)
        recipe.test_script = "test.sh"
        (recipe.recipe_dir / "test.sh").write_text("# fake haiku test script\n")
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True
        # A delegated build already synced an install tree back to this side.
        (ctx.install_dir / "lib").mkdir(parents=True)
        (ctx.install_dir / "lib" / "libfake.so").write_text("fake")
        return host, recipe, ctx

    def test_stages_and_runs_the_test_remotely(self, tmp_path, monkeypatch):
        host, recipe, ctx = self._tested(tmp_path, monkeypatch)

        haikuhost.run_haiku_test(ctx, recipe.recipe_dir / "test.sh")

        (job_dir,) = host.job_dirs
        assert "-test-" in job_dir.name
        # The artifact under test, its deps and the recipe travel; the source
        # tree and the build dir do not.
        assert (job_dir / "install" / "lib" / "libfake.so").is_file()
        assert (job_dir / "deps" / "lib" / "pkgconfig" / "zlib.pc").is_file()
        assert (job_dir / "recipe" / recipe.name / "test.sh").is_file()
        assert (job_dir / "recipe" / "_common" / "env-haiku.sh").is_file()
        assert not (job_dir / "source").exists()

        runner = (job_dir / "run-job.sh").read_text()
        env = runner_env(runner)
        remote_job = f"/boot/home/cvcpkg-build/jobs/{job_dir.name}"
        assert env["CVC_INSTALL_DIR"] == remote_job + "/install"
        assert env["CVC_PREFIX"] == remote_job + "/install"
        assert env["CVC_DEPS_PREFIX"] == remote_job + "/deps"
        assert env["CVC_PLATFORM"] == "haiku"
        assert env["CVC_HAIKUHOST"] == "1"
        # run_test's contract: the install dir is the cwd.
        assert 'cd "$CVC_INSTALL_DIR"' in runner
        assert 'exec bash "$_script"' in runner

    def test_remote_failure_raises_and_keeps_the_dir(self, tmp_path, monkeypatch):
        host, recipe, ctx = self._tested(tmp_path, monkeypatch, run_result=4)
        with pytest.raises(haikuhost.HaikuhostError, match="test of fakelib failed"):
            haikuhost.run_haiku_test(ctx, recipe.recipe_dir / "test.sh")
        assert len(host.job_dirs) == 1

    def test_success_cleans_up(self, tmp_path, monkeypatch):
        host, recipe, ctx = self._tested(tmp_path, monkeypatch)
        ctx.keep_build_dir = False
        haikuhost.run_haiku_test(ctx, recipe.recipe_dir / "test.sh")
        assert host.job_dirs == []

    def test_unconfigured_host_is_actionable(self, tmp_path, monkeypatch):
        recipe = _make_recipe(tmp_path)
        (recipe.recipe_dir / "test.sh").write_text("# fake\n")
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(haikuhost.HaikuhostError, match="CVCPKG_HAIKU_SSH"):
            haikuhost.run_haiku_test(ctx, recipe.recipe_dir / "test.sh")

    def test_script_is_named_relative_to_the_recipe_dir(self, tmp_path, monkeypatch):
        """A recipe may keep its tests in a subdirectory; only the recipe dir
        travels, so the runner must name the script by its relative path."""
        host, recipe, ctx = self._tested(tmp_path, monkeypatch)
        (recipe.recipe_dir / "tests").mkdir()
        nested = recipe.recipe_dir / "tests" / "smoke.sh"
        nested.write_text("# fake\n")

        haikuhost.run_haiku_test(ctx, nested)

        runner = (host.job_dirs[0] / "run-job.sh").read_text()
        assert '_script="$CVC_RECIPE_DIR/"tests/smoke.sh' in runner

    def test_script_outside_the_recipe_dir_rejected(self, tmp_path, monkeypatch):
        host, recipe, ctx = self._tested(tmp_path, monkeypatch)
        stray = tmp_path / "stray.sh"
        stray.write_text("# fake\n")
        with pytest.raises(haikuhost.HaikuhostError, match="outside the recipe dir"):
            haikuhost.run_haiku_test(ctx, stray)

    def test_non_sh_test_script_rejected(self, tmp_path, monkeypatch):
        host, recipe, ctx = self._tested(tmp_path, monkeypatch)
        (recipe.recipe_dir / "test.ps1").write_text("# fake\n")
        with pytest.raises(haikuhost.HaikuhostError, match="only supports .sh"):
            haikuhost.run_haiku_test(ctx, recipe.recipe_dir / "test.ps1")


# ── reachability probe ──────────────────────────────────────────


class TestAvailability:
    def test_unconfigured_is_unavailable(self):
        assert haikuhost.haikuhost_available() is False

    def test_probe_uses_batchmode(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        seen: dict = {}

        def _fake_run(cmd, **kwargs):
            seen["cmd"] = cmd

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(haikuhost.subprocess, "run", _fake_run)
        assert haikuhost.haikuhost_available() is True
        assert "BatchMode=yes" in seen["cmd"]
        assert seen["cmd"][-1] == "true"

    def test_unreachable_host(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")

        def _fake_run(cmd, **kwargs):
            raise OSError("no route to host")

        monkeypatch.setattr(haikuhost.subprocess, "run", _fake_run)
        assert haikuhost.haikuhost_available() is False


# ── run_build hook ──────────────────────────────────────────────


class TestRunBuildHook:
    def test_run_build_delegates_to_haikuhost(self, tmp_path, monkeypatch):
        """run_build routes haiku builds through the haikuhost module."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        called = {}

        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: True)

        def _fake_run(ctx_arg, matrix, script, log_callback=None):
            called["recipe"] = ctx_arg.recipe.name
            called["script"] = script.name

        monkeypatch.setattr(haikuhost, "run_haiku_build", _fake_run)
        builder_mod.run_build(ctx)
        assert called == {"recipe": "fakelib", "script": "build.sh"}

    def test_delegation_precedes_the_local_toolchain(self, tmp_path, monkeypatch):
        """The hook must sit ahead of the local interpreter lookup: running a
        haiku build.sh through the LOCAL bash would 'succeed' and package
        Linux binaries as haiku/x86_64."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: True)
        monkeypatch.setattr(haikuhost, "run_haiku_build", lambda *a, **k: None)
        monkeypatch.setattr(
            builder_mod,
            "_find_bash",
            lambda: (_ for _ in ()).throw(AssertionError("local toolchain reached")),
        )
        builder_mod.run_build(ctx)  # must not raise

    def test_run_build_native_path_untouched(self, tmp_path, monkeypatch):
        """When delegation declines, run_build proceeds down the local path."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: False)
        monkeypatch.setattr(
            builder_mod,
            "_find_bash",
            lambda: (_ for _ in ()).throw(builder_mod.BuildError("bash probe")),
        )
        with pytest.raises(builder_mod.BuildError, match="bash probe"):
            builder_mod.run_build(ctx)

    def test_delegated_build_still_gets_the_origin_rpath_pass(self, tmp_path, monkeypatch):
        """The delegated path used to `return` before the relocation pass, so
        adding haiku to _ELF_RPATH_PLATFORMS was dead code and every non-CMake
        Haiku bundle shipped with absolute build-tree RPATHs.  patchelf runs
        here, on the copied-back tree: it edits ELF as data, and Haiku is ELF."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        seen: list[Path] = []
        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: True)
        monkeypatch.setattr(haikuhost, "run_haiku_build", lambda *a, **k: None)
        monkeypatch.setattr(builder_mod, "_find_patchelf", lambda *a: "/usr/bin/patchelf")
        monkeypatch.setattr(builder_mod, "_patch_elf_rpath", lambda d, p=None: seen.append(Path(d)))
        builder_mod.run_build(ctx)
        assert seen == [ctx.install_dir]

    def test_static_delegated_build_skips_the_rpath_pass(self, tmp_path, monkeypatch):
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.link = "static"
        seen: list[Path] = []
        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: True)
        monkeypatch.setattr(haikuhost, "run_haiku_build", lambda *a, **k: None)
        monkeypatch.setattr(builder_mod, "_patch_elf_rpath", lambda d, p=None: seen.append(Path(d)))
        builder_mod.run_build(ctx)
        assert seen == []


class TestFailFast:
    """A builder that cannot delegate must say so BEFORE it does any work.

    ``run_build`` is the last line of defence, but it only runs after
    ``build_recipe`` has fetched, extracted and patched the source — minutes
    and gigabytes spent on a job whose outcome a missing CVCPKG_HAIKU_SSH
    already decided.
    """

    def test_build_recipe_refuses_before_fetching(self, tmp_path, monkeypatch):
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        monkeypatch.setattr(builder_mod.Recipe, "load", staticmethod(lambda d: recipe))
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        monkeypatch.setattr(
            builder_mod,
            "fetch_source",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched anyway")),
        )
        with pytest.raises(haikuhost.HaikuhostError, match="CVCPKG_HAIKU_SSH"):
            builder_mod.build_recipe(recipe.recipe_dir, platform="haiku", host_platform="linux")

    def test_build_all_refuses_before_the_first_recipe(self, tmp_path, monkeypatch):
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        monkeypatch.setattr(builder_mod, "list_recipes", lambda d: [recipe])
        monkeypatch.setattr(
            builder_mod,
            "resolve_build_order",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("started the run")),
        )
        with pytest.raises(haikuhost.HaikuhostError, match="CVCPKG_HAIKU_SSH"):
            builder_mod.build_all(tmp_path / "recipes", platform="haiku", host_platform="linux")

    def test_a_configured_builder_gets_past_the_gate(self, tmp_path, monkeypatch):
        """The gate must not stand in the way of a builder that IS configured."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        monkeypatch.setenv("CVCPKG_HAIKU_SSH", "user@haiku-build")
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        monkeypatch.setattr(builder_mod.Recipe, "load", staticmethod(lambda d: recipe))
        monkeypatch.setattr(
            builder_mod,
            "fetch_source",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached the fetch")),
        )
        with pytest.raises(RuntimeError, match="reached the fetch"):
            builder_mod.build_recipe(recipe.recipe_dir, platform="haiku", host_platform="linux")

    def test_a_non_haiku_build_is_unaffected(self, tmp_path, monkeypatch):
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        monkeypatch.setattr(haikuhost, "is_haiku", lambda: False)
        monkeypatch.setattr(builder_mod.Recipe, "load", staticmethod(lambda d: recipe))
        monkeypatch.setattr(
            builder_mod,
            "fetch_source",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached the fetch")),
        )
        with pytest.raises(RuntimeError, match="reached the fetch"):
            builder_mod.build_recipe(recipe.recipe_dir, platform="linux", host_platform="linux")


class TestRunTestHook:
    def test_run_test_delegates_to_the_haiku_host(self, tmp_path, monkeypatch):
        """A Haiku bundle must not be published without a Haiku binary ever
        having run: the local test process would exercise the builder's own
        Linux tools against a tree of Haiku ELF."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        recipe.test_script = "test.sh"
        (recipe.recipe_dir / "test.sh").write_text("# fake\n")
        ctx = _make_ctx(tmp_path, recipe)

        called: dict = {}
        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: True)
        monkeypatch.setattr(
            haikuhost,
            "run_haiku_test",
            lambda c, path, log_callback=None: called.update(script=path.name),
        )
        monkeypatch.setattr(
            builder_mod,
            "_find_bash",
            lambda: (_ for _ in ()).throw(AssertionError("local test run reached")),
        )
        builder_mod.run_test(ctx)
        assert called == {"script": "test.sh"}

    def test_local_test_path_untouched(self, tmp_path, monkeypatch):
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        recipe.test_script = "test.sh"
        (recipe.recipe_dir / "test.sh").write_text("# fake\n")
        ctx = _make_ctx(tmp_path, recipe)
        monkeypatch.setattr(haikuhost, "should_delegate", lambda p, h: False)
        monkeypatch.setattr(
            builder_mod,
            "_find_bash",
            lambda: (_ for _ in ()).throw(builder_mod.BuildError("bash probe")),
        )
        with pytest.raises(builder_mod.BuildError, match="bash probe"):
            builder_mod.run_test(ctx)
