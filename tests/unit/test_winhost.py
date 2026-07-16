"""WSL -> Windows host build delegation (``cvcpkg.winhost``).

The interop boundary (powershell.exe, wslpath) is faked so the tests run
anywhere; what is exercised is the decision logic, the exchange-dir job
staging (path translation, dep prefix rewriting, env construction), the
install-tree sync-back, and the ``run_build`` hook.
"""

import json
from pathlib import Path

import pytest

from cvcpkg import winhost
from cvcpkg.builder import BuildContext, MatrixEntry, Recipe, SourceSpec


@pytest.fixture(autouse=True)
def _reset_interop_cache():
    winhost._interop_cache = None
    yield
    winhost._interop_cache = None


# ── should_delegate ─────────────────────────────────────────────


class TestShouldDelegate:
    def _wsl(self, monkeypatch, value=True):
        monkeypatch.setattr(winhost, "is_wsl", lambda: value)
        monkeypatch.setattr(winhost.sys, "platform", "linux")

    def test_delegates_windows_cross_from_wsl(self, monkeypatch):
        self._wsl(monkeypatch)
        assert winhost.should_delegate("windows", "linux") is True

    def test_win_alias(self, monkeypatch):
        self._wsl(monkeypatch)
        assert winhost.should_delegate("win", "linux") is True

    def test_native_windows_build_not_delegated(self, monkeypatch):
        self._wsl(monkeypatch)
        assert winhost.should_delegate("windows", "") is False
        assert winhost.should_delegate("windows", "windows") is False

    def test_non_windows_target_not_delegated(self, monkeypatch):
        self._wsl(monkeypatch)
        assert winhost.should_delegate("wasm", "linux") is False
        assert winhost.should_delegate("linux", "linux") is False

    def test_not_in_wsl_not_delegated(self, monkeypatch):
        self._wsl(monkeypatch, value=False)
        assert winhost.should_delegate("windows", "linux") is False

    def test_env_kill_switch(self, monkeypatch):
        self._wsl(monkeypatch)
        monkeypatch.setenv("CVCPKG_WINHOST", "0")
        assert winhost.should_delegate("windows", "linux") is False

    def test_not_on_linux(self, monkeypatch):
        monkeypatch.setattr(winhost, "is_wsl", lambda: True)
        monkeypatch.setattr(winhost.sys, "platform", "win32")
        assert winhost.should_delegate("windows", "linux") is False


# ── helpers to fabricate a build context ────────────────────────


def _make_recipe(tmp_path: Path, name="fakelib") -> Recipe:
    recipes_root = tmp_path / "recipes"
    rdir = recipes_root / name
    rdir.mkdir(parents=True)
    (rdir / "recipe.yaml").write_text(f"recipe: {{name: {name}}}\n")
    (rdir / "build.ps1").write_text("# fake windows build script\n")
    common = recipes_root / "_common"
    common.mkdir()
    (common / winhost.RUNNER_NAME).write_text("# fake runner\n")
    return Recipe(
        name=name,
        upstream_version="1.0.0",
        cvc_revision=1,
        source=SourceSpec(type="tarball", url="https://example.invalid/src.tar.gz"),
        patches=[],
        build_matrix=[MatrixEntry(platform="windows", script="build.ps1")],
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
        platform="windows",
        config="release",
        link="shared",
        prefix=deps,
        source_dir=src,
        build_dir=work / "build",
        install_dir=work / "install",
        work_dir=work,
        host_platform="linux",
    )


class _FakeHost:
    """Fakes the interop boundary: wslpath + powershell + runner exec.

    The 'Windows profile' lives under tmp_path/winprofile; Windows paths
    are spelled ``W:\\...`` and map 1:1 onto that directory so the module's
    path arithmetic is exercised for real.
    """

    def __init__(self, tmp_path: Path, monkeypatch, run_result=0):
        self.root = tmp_path / "winprofile"
        self.root.mkdir()
        self.run_calls: list[list[str]] = []
        self.run_result = run_result
        self.installed_files = ["lib/fake.lib", "include/fake.h"]

        monkeypatch.setattr(
            winhost,
            "_resolve_interop",
            lambda force=False: {"powershell": "/fake/powershell.exe", "env": {}},
        )
        monkeypatch.setattr(winhost, "win_user_profile", lambda: "W:\\profile")
        monkeypatch.setattr(winhost, "win_path", self._to_win)
        monkeypatch.setattr(winhost, "wsl_path", self._to_wsl)
        monkeypatch.setattr(winhost, "_stream_host_process", self._run)

    def _to_win(self, path) -> str:
        p = str(Path(path).resolve())
        return "W:" + p.replace("/", "\\")

    def _to_wsl(self, winpath: str) -> Path:
        assert winpath.startswith("W:\\profile"), winpath
        rel = winpath[len("W:\\profile") :].replace("\\", "/").lstrip("/")
        return self.root / rel if rel else self.root

    def _run(self, cmd, env, log_callback):
        self.run_calls.append(cmd)
        # Emulate the host build: read the job file, drop files into the
        # install dir it names.
        job_file_win = cmd[cmd.index("-JobFile") + 1]
        job_file = self._to_wsl(job_file_win)
        job = json.loads(job_file.read_text())
        install = self._to_wsl(job["env"]["CVC_INSTALL_DIR"])
        if self.run_result == 0:
            for rel in self.installed_files:
                out = install / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("fake")
        return self.run_result


# ── exchange-mode staging + sync-back ───────────────────────────


class TestExchangeMode:
    @pytest.fixture(autouse=True)
    def _force_exchange(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_WINHOST_MODE", "exchange")
        monkeypatch.delenv("CVCPKG_WINHOST_EXCHANGE", raising=False)

    def test_stages_builds_and_syncs_back(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        logs: list[str] = []
        winhost.run_winhost_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", logs.append
        )

        # Install tree synced back to the Linux side.
        assert (ctx.install_dir / "lib" / "fake.lib").is_file()
        assert (ctx.install_dir / "include" / "fake.h").is_file()

        # Runner was invoked with -File <runner> -JobFile <job>.
        (cmd,) = host.run_calls
        assert cmd[cmd.index("-File") + 1].endswith(winhost.RUNNER_NAME)

        # Job dir cleaned up after success (keep_build_dir=False).
        jobs_root = host.root / "cvcpkg-winhost" / "jobs"
        assert not any(jobs_root.iterdir())

    def test_job_env_and_dep_rewrite(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True  # keep the job dir for inspection

        winhost.run_winhost_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
        )

        jobs_root = host.root / "cvcpkg-winhost" / "jobs"
        (job_dir,) = list(jobs_root.iterdir())

        # Staged trees.
        assert (job_dir / "source" / "hello.c").is_file()
        assert (job_dir / "source" / "sub" / "x.h").is_file()
        assert (job_dir / "recipe" / recipe.name / "build.ps1").is_file()
        assert (job_dir / "recipe" / "_common" / winhost.RUNNER_NAME).is_file()
        assert (job_dir / "deps" / "bin").is_dir()

        # Job env points at Windows-side paths and carries build settings.
        job = json.loads((job_dir / "winhost-job.json").read_text())
        env = job["env"]
        win_job = "W:\\profile\\cvcpkg-winhost\\jobs\\" + job_dir.name
        assert env["CVC_SOURCE_DIR"] == win_job + "\\source"
        assert env["CVC_INSTALL_DIR"] == win_job + "\\install"
        assert env["CVC_DEPS_PREFIX"] == win_job + "\\deps"
        assert env["CVC_PLATFORM"] == "windows"
        assert env["CVC_WINHOST"] == "1"
        assert env["CVC_COMPONENT"] == recipe.name
        assert env["CMAKE_BUILD_TYPE"] == "Release"
        assert env["BUILD_SHARED_LIBS"] == "ON"

        # Dep .pc prefix rewritten from the Linux path to the exchange path.
        pc = (job_dir / "deps" / "lib" / "pkgconfig" / "zlib.pc").read_text()
        assert str(ctx.prefix) not in pc
        assert "W:/profile/cvcpkg-winhost/jobs/" in pc

    def test_matrix_env_overrides_win(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.build_matrix[0].env = {"CFLAGS": "/O2", "CVC_LINK": "static"}
        ctx = _make_ctx(tmp_path, recipe)
        ctx.keep_build_dir = True

        winhost.run_winhost_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
        )
        (job_dir,) = list((host.root / "cvcpkg-winhost" / "jobs").iterdir())
        env = json.loads((job_dir / "winhost-job.json").read_text())["env"]
        assert env["CFLAGS"] == "/O2"
        assert env["CVC_LINK"] == "static"  # matrix env wins

    def test_host_failure_raises_and_cleans_up(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch, run_result=7)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        with pytest.raises(winhost.WinhostError, match="exit code 7"):
            winhost.run_winhost_build(
                ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
            )
        jobs_root = host.root / "cvcpkg-winhost" / "jobs"
        assert not any(jobs_root.iterdir())

    def test_empty_install_tree_fails(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch)
        host.installed_files = []
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        with pytest.raises(winhost.WinhostError, match="installed no files"):
            winhost.run_winhost_build(
                ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
            )

    def test_exchange_override_env(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_WINHOST_EXCHANGE", "W:\\profile\\custom-x")
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        winhost.run_winhost_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
        )
        assert (host.root / "custom-x" / "jobs").is_dir()

    def test_non_ps1_script_rejected(self, tmp_path, monkeypatch):
        _FakeHost(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        recipe.build_matrix[0].script = "build.sh"
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(winhost.WinhostError, match="only supports .ps1"):
            winhost.run_winhost_build(
                ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.sh", None
            )

    def test_missing_runner_is_actionable(self, tmp_path, monkeypatch):
        _FakeHost(tmp_path, monkeypatch)
        recipe = _make_recipe(tmp_path)
        (recipe.recipe_dir.parent / "_common" / winhost.RUNNER_NAME).unlink()
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(winhost.WinhostError, match="push current recipes"):
            winhost.run_winhost_build(
                ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
            )


# ── direct mode ─────────────────────────────────────────────────


class TestDirectMode:
    def test_direct_paths_and_dep_rewrite(self, tmp_path, monkeypatch):
        host = _FakeHost(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_WINHOST_MODE", "direct")
        monkeypatch.setattr(winhost, "_probe_direct_access", lambda d: True)

        # In direct mode the "install dir the host writes to" is the WSL
        # dir itself (via UNC); mimic by writing through _to_wsl on W: of
        # the resolved path.
        def _run_direct(cmd, env, log_callback):
            host.run_calls.append(cmd)
            job_file_win = cmd[cmd.index("-JobFile") + 1]
            job_file = Path(job_file_win[2:].replace("\\", "/"))
            job = json.loads(job_file.read_text())
            install = Path(job["env"]["CVC_INSTALL_DIR"][2:].replace("\\", "/"))
            (install / "lib").mkdir(parents=True, exist_ok=True)
            (install / "lib" / "direct.lib").write_text("fake")
            return 0

        monkeypatch.setattr(winhost, "_stream_host_process", _run_direct)

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        winhost.run_winhost_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
        )

        # Host wrote straight into the WSL-side install dir; no sync needed.
        assert (ctx.install_dir / "lib" / "direct.lib").is_file()
        # Dep .pc rewritten to the host-visible spelling of the deps dir.
        pc = (ctx.prefix / "lib" / "pkgconfig" / "zlib.pc").read_text()
        assert f"prefix=W:{ctx.prefix.resolve()}".replace("\\", "/") in pc.replace("\\", "/")

    def test_auto_selects_exchange(self, tmp_path, monkeypatch):
        """auto never picks direct — cmd.exe/CMake break on UNC cwds."""
        _FakeHost(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_WINHOST_MODE", "auto")

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        winhost.run_winhost_build(
            ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
        )
        # Exchange staging happened.
        assert (ctx.install_dir / "lib" / "fake.lib").is_file()

    def test_direct_requires_host_visibility(self, tmp_path, monkeypatch):
        _FakeHost(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_WINHOST_MODE", "direct")
        monkeypatch.setattr(winhost, "_probe_direct_access", lambda d: False)
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(winhost.WinhostError, match="cannot access"):
            winhost.run_winhost_build(
                ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
            )

    def test_invalid_mode_rejected(self, tmp_path, monkeypatch):
        _FakeHost(tmp_path, monkeypatch)
        monkeypatch.setenv("CVCPKG_WINHOST_MODE", "bogus")
        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        with pytest.raises(winhost.WinhostError, match="invalid CVCPKG_WINHOST_MODE"):
            winhost.run_winhost_build(
                ctx, recipe.build_matrix[0], recipe.recipe_dir / "build.ps1", None
            )


# ── run_build hook ──────────────────────────────────────────────


class TestRunBuildHook:
    def test_run_build_delegates_to_winhost(self, tmp_path, monkeypatch):
        """run_build routes windows-cross builds through the winhost module."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)

        called = {}

        monkeypatch.setattr(winhost, "should_delegate", lambda p, h: True)

        def _fake_run(ctx_arg, matrix, script, log_callback=None):
            called["recipe"] = ctx_arg.recipe.name
            called["script"] = script.name

        monkeypatch.setattr(winhost, "run_winhost_build", _fake_run)
        builder_mod.run_build(ctx)
        assert called == {"recipe": "fakelib", "script": "build.ps1"}

    def test_run_build_native_path_untouched(self, tmp_path, monkeypatch):
        """When delegation declines, run_build proceeds down the local path."""
        from cvcpkg import builder as builder_mod

        recipe = _make_recipe(tmp_path)
        ctx = _make_ctx(tmp_path, recipe)
        monkeypatch.setattr(winhost, "should_delegate", lambda p, h: False)

        # Local path will look for pwsh; make _find_pwsh fail loudly to
        # prove we reached it (i.e. delegation was not taken).
        monkeypatch.setattr(
            builder_mod,
            "_find_pwsh",
            lambda prefix=None: (_ for _ in ()).throw(builder_mod.BuildError("pwsh probe")),
        )
        with pytest.raises(builder_mod.BuildError, match="pwsh probe"):
            builder_mod.run_build(ctx)


# ── interop resolution ──────────────────────────────────────────


class TestInteropResolution:
    def test_socket_candidates_prefer_env_then_init(self, tmp_path, monkeypatch):
        run_wsl = tmp_path / "run-WSL"
        run_wsl.mkdir()
        (run_wsl / "1_interop").write_text("")
        (run_wsl / "99_interop").write_text("")
        monkeypatch.setenv("WSL_INTEROP", str(run_wsl / "99_interop"))
        monkeypatch.setattr(
            winhost.glob,
            "glob",
            lambda pat: (
                [str(run_wsl / "1_interop"), str(run_wsl / "99_interop")]
                if "*_interop" in pat
                else []
            ),
        )
        monkeypatch.setattr(
            winhost.os.path,
            "exists",
            lambda p: str(p).startswith(str(run_wsl)) or Path(p).exists(),
        )
        cands = winhost._interop_socket_candidates()
        assert cands[0] == str(run_wsl / "99_interop")  # env first

    def test_resolve_interop_no_powershell(self, monkeypatch):
        monkeypatch.setattr(winhost, "_powershell_candidates", lambda: [])
        with pytest.raises(winhost.WinhostError, match="powershell.exe not found"):
            winhost._resolve_interop(force=True)

    def test_resolve_interop_probes_and_caches(self, monkeypatch):
        calls = []

        def _fake_run(cmd, env=None, capture_output=True, timeout=60):
            calls.append(env.get("WSL_INTEROP"))

            class R:
                returncode = 0 if len(calls) > 1 else 1
                stderr = b"nope"

            return R()

        monkeypatch.setattr(winhost, "_powershell_candidates", lambda: ["/fake/ps.exe"])
        monkeypatch.setattr(winhost, "_interop_socket_candidates", lambda: ["/run/WSL/1_interop"])
        monkeypatch.setattr(winhost.subprocess, "run", _fake_run)

        io = winhost._resolve_interop(force=True)
        # First attempt (inherited env) failed; second (explicit socket) worked.
        assert io["env"] == {"WSL_INTEROP": "/run/WSL/1_interop"}
        # Cached now.
        assert winhost._resolve_interop() is io


class TestExchangeOverride:
    """CVCPKG_WINHOST_EXCHANGE parsing — forward-slash normalization and
    detection of systemd EnvironmentFile backslash-stripping (dev job
    #52, 2026-07-15)."""

    def test_unset_returns_empty(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_WINHOST_EXCHANGE", raising=False)
        assert winhost._exchange_override() == ""

    def test_backslash_form_passes_through(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_WINHOST_EXCHANGE", "C:\\Users\\tfx\\cvc-exchange")
        assert winhost._exchange_override() == "C:\\Users\\tfx\\cvc-exchange"

    def test_forward_slash_form_normalized(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_WINHOST_EXCHANGE", "C:/Users/tfx/cvc-exchange/")
        assert winhost._exchange_override() == "C:\\Users\\tfx\\cvc-exchange"

    def test_systemd_mangled_value_raises(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_WINHOST_EXCHANGE", "C:Userstfxcvcpkg-winhost-sandipaws-wsl")
        with pytest.raises(winhost.WinhostError, match="EnvironmentFile"):
            winhost._exchange_override()
