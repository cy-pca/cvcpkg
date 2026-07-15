"""Tests for cvcpkg.activate — activation script generator."""

from __future__ import annotations

import pytest

from cvcpkg import activate as act


class TestLibVarSelection:
    def test_macos_uses_dyld(self):
        assert act._lib_var_for_platform("macos") == "DYLD_LIBRARY_PATH"

    @pytest.mark.parametrize("plat", ["linux", "freebsd", "openbsd", "netbsd", "wasi"])
    def test_posix_uses_ld(self, plat):
        assert act._lib_var_for_platform(plat) == "LD_LIBRARY_PATH"


class TestRendering:
    def test_bash_render_contains_prefix_and_deactivate(self, tmp_path):
        import re

        text = act.render_bash(tmp_path / "myprefix", prompt="myprefix", platform="linux")
        assert "cvcpkg_deactivate" in text
        assert (tmp_path / "myprefix").as_posix() in text
        assert "CVCPKG_ACTIVE_PREFIX" in text
        # \bLD_LIBRARY_PATH\b matches on Linux but not DYLD_LIBRARY_PATH on macOS.
        assert re.search(r"\bLD_LIBRARY_PATH\b", text)
        assert not re.search(r"\bDYLD_LIBRARY_PATH\b", text)
        # sourced-only guard
        assert "must be sourced" in text
        # no unresolved placeholder tokens
        assert "__CVCPKG_" not in text

    def test_bash_macos_uses_dyld(self, tmp_path):
        import re

        text = act.render_bash(tmp_path / "p", prompt="p", platform="macos")
        assert re.search(r"\bDYLD_LIBRARY_PATH\b", text)
        assert not re.search(r"\bLD_LIBRARY_PATH\b", text)

    def test_fish_render(self, tmp_path):
        text = act.render_fish(tmp_path / "p", prompt="p", platform="linux")
        assert "function cvcpkg_deactivate" in text
        assert "set -gx PATH" in text
        assert "__CVCPKG_" not in text

    def test_csh_render(self, tmp_path):
        text = act.render_csh(tmp_path / "p", prompt="p", platform="linux")
        assert "alias cvcpkg_deactivate" in text
        assert "setenv PATH" in text
        assert "__CVCPKG_" not in text

    def test_powershell_render(self, tmp_path):
        text = act.render_powershell(tmp_path / "p", prompt="p")
        assert "cvcpkg_deactivate" in text
        assert "$env:PATH" in text
        assert "Activate.ps1" in text
        assert "__CVCPKG_" not in text

    def test_cmd_render(self, tmp_path):
        text = act.render_cmd(tmp_path / "p", prompt="p")
        assert "CVCPKG_ACTIVE_PREFIX" in text
        assert 'set "PATH=' in text
        assert "__CVCPKG_" not in text

    def test_cmd_deactivate_render(self):
        text = act.render_cmd_deactivate()
        assert "_CVCPKG_OLD_PATH" in text
        assert "__CVCPKG_" not in text


class TestWriteActivateScripts:
    def test_posix_writes_three_scripts(self, tmp_path):
        prefix = tmp_path / "posix-prefix"
        prefix.mkdir()
        written = act.write_activate_scripts(prefix, platform="linux")
        names = sorted(p.name for p in written)
        assert names == ["activate", "activate.csh", "activate.fish"]
        for p in written:
            assert p.parent == prefix / "bin"
            assert p.is_file()
            assert p.stat().st_size > 0

    def test_macos_uses_dyld_in_written_files(self, tmp_path):
        import re

        prefix = tmp_path / "mac-prefix"
        prefix.mkdir()
        written = act.write_activate_scripts(prefix, platform="macos")
        bash = next(p for p in written if p.name == "activate")
        text = bash.read_text()
        assert re.search(r"\bDYLD_LIBRARY_PATH\b", text)
        assert not re.search(r"\bLD_LIBRARY_PATH\b", text)

    def test_windows_writes_scripts_dir(self, tmp_path):
        prefix = tmp_path / "win-prefix"
        prefix.mkdir()
        written = act.write_activate_scripts(prefix, platform="windows")
        names = sorted(p.name for p in written)
        assert names == ["Activate.ps1", "activate.bat", "cvcpkg_deactivate.bat"]
        for p in written:
            assert p.parent == prefix / "Scripts"
            assert p.is_file()

    def test_default_prompt_is_prefix_basename(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        act.write_activate_scripts(prefix, platform="linux")
        text = (prefix / "bin" / "activate").read_text()
        assert "(deps)" in text

    def test_custom_prompt(self, tmp_path):
        prefix = tmp_path / "deps"
        prefix.mkdir()
        act.write_activate_scripts(prefix, platform="linux", prompt="my-env")
        text = (prefix / "bin" / "activate").read_text()
        assert "(my-env)" in text

    def test_creates_bin_dir(self, tmp_path):
        prefix = tmp_path / "fresh"
        # no mkdir — prefix doesn't exist yet
        written = act.write_activate_scripts(prefix, platform="linux")
        assert (prefix / "bin").is_dir()
        assert len(written) == 3


class TestBashSemantics:
    """Sanity-check the generated bash script actually runs."""

    @pytest.mark.skipif(
        __import__("sys").platform == "win32",
        reason="MSYS bash on Windows mangles native paths; separator collision with C:\\",
    )
    def test_bash_activate_deactivate_cycle(self, tmp_path):
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash not available")

        prefix = tmp_path / "prefix"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "lib").mkdir()
        (prefix / "lib" / "pkgconfig").mkdir()
        act.write_activate_scripts(prefix, platform="linux")

        script = f"""
set -e
export PATH=/orig/path
export PS1='> '
unset CMAKE_PREFIX_PATH PKG_CONFIG_PATH LD_LIBRARY_PATH
source '{prefix}/bin/activate'
echo "AFTER_ACTIVATE_PATH=${{PATH%%:*}}"
echo "CMAKE=$CMAKE_PREFIX_PATH"
echo "PKG=$PKG_CONFIG_PATH"
echo "LIB=$LD_LIBRARY_PATH"
echo "ACTIVE=$CVCPKG_ACTIVE_PREFIX"
cvcpkg_deactivate
echo "AFTER_DEACTIVATE_PATH=$PATH"
echo "CMAKE_AFTER=${{CMAKE_PREFIX_PATH-unset}}"
echo "LIB_AFTER=${{LD_LIBRARY_PATH-unset}}"
echo "ACTIVE_AFTER=${{CVCPKG_ACTIVE_PREFIX-unset}}"
"""
        result = subprocess.run([bash, "-c", script], capture_output=True, text=True, check=True)
        out = result.stdout
        assert f"AFTER_ACTIVATE_PATH={prefix}/bin" in out
        assert f"CMAKE={prefix}" in out
        assert f"{prefix}/lib/pkgconfig" in out
        assert f"LIB={prefix}/lib" in out
        assert f"ACTIVE={prefix}" in out
        assert "AFTER_DEACTIVATE_PATH=/orig/path" in out
        assert "CMAKE_AFTER=unset" in out
        assert "LIB_AFTER=unset" in out
        assert "ACTIVE_AFTER=unset" in out

    def test_bash_refuses_direct_execution(self, tmp_path):
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash not available")

        prefix = tmp_path / "prefix"
        prefix.mkdir()
        written = act.write_activate_scripts(prefix, platform="linux")
        activate = next(p for p in written if p.name == "activate")

        result = subprocess.run([bash, str(activate)], capture_output=True, text=True)
        assert result.returncode == 33
        assert "must be sourced" in result.stderr


def _can_symlink() -> bool:
    """True when this process may create symlinks.

    On Windows, os.symlink needs an elevated token or Developer Mode
    (SeCreateSymbolicLinkPrivilege); a plain user shell raises OSError
    WinError 1314.  CI runners are elevated, so these tests still run
    there — only unprivileged dev boxes skip.
    """
    import os
    import tempfile
    import uuid

    probe = os.path.join(tempfile.gettempdir(), f"cvc-symlink-probe-{uuid.uuid4().hex}")
    try:
        os.symlink("probe-target", probe)
    except (OSError, NotImplementedError):
        return False
    else:
        os.unlink(probe)
        return True


@pytest.mark.skipif(
    not _can_symlink(),
    reason="symlink creation not permitted (Windows without elevation/Developer Mode)",
)
class TestPythonAliasReconcile:
    """write_activate_scripts surfaces generic python/pip commands for
    install trees whose python was staged as versioned-only binaries."""

    def _versioned(self, bin_dir, *names):
        bin_dir.mkdir(parents=True, exist_ok=True)
        for n in names:
            (bin_dir / n).write_text("#!/bin/sh\n")

    def test_creates_generic_commands(self, tmp_path):
        bin_dir = tmp_path / "bin"
        self._versioned(bin_dir, "python3.13", "python3.13-config", "pip3.13")
        act.write_activate_scripts(tmp_path, platform="linux")
        for name in ("python3", "python", "python3-config", "pip3", "pip"):
            assert (bin_dir / name).is_symlink(), f"{name} should be created"
        assert (bin_dir / "python3").resolve() == (bin_dir / "python3.13")
        assert (bin_dir / "pip").resolve() == (bin_dir / "pip3.13")

    def test_picks_highest_version(self, tmp_path):
        bin_dir = tmp_path / "bin"
        self._versioned(bin_dir, "python3.11", "python3.13", "python3.9")
        act.write_activate_scripts(tmp_path, platform="linux")
        assert (bin_dir / "python3").resolve() == (bin_dir / "python3.13")

    def test_respects_existing_alias(self, tmp_path):
        bin_dir = tmp_path / "bin"
        self._versioned(bin_dir, "python3.11", "python3.13")
        (bin_dir / "python3").symlink_to("python3.11")  # meta/user pinned 3.11
        act.write_activate_scripts(tmp_path, platform="linux")
        assert (bin_dir / "python3").resolve() == (bin_dir / "python3.11")  # not clobbered
        assert (bin_dir / "python").resolve() == (bin_dir / "python3.13")  # bare created -> highest

    def test_ignores_free_threaded_for_generic(self, tmp_path):
        bin_dir = tmp_path / "bin"
        self._versioned(bin_dir, "python3.13t")  # free-threaded only
        act.write_activate_scripts(tmp_path, platform="linux")
        assert not (bin_dir / "python3").exists()  # 't' build never becomes generic python3
