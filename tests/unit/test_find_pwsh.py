"""``builder._find_pwsh``: a cvcpkg pwsh 7 first, then PATH, then Windows PowerShell.

A Windows box with no pwsh 7 (stock Windows 11, e.g. a lanparty-gamez user's)
failed every ``build.ps1`` with "pwsh 7 not found", and declaring the
``powershell`` host tool did not help: ``--with-deps`` builds it from its recipe
into the build prefix, its own build.ps1 needed a pwsh to run, and the targets
only looked for pwsh in the deliverable prefix.
"""

from pathlib import Path

import pytest

from cvcpkg import builder

RECIPES = Path(__file__).resolve().parents[2] / "recipes"


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """A Windows host with Windows PowerShell 5.1 and no pwsh on PATH."""
    monkeypatch.setattr(builder.sys, "platform", "win32")
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    root = tmp_path / "Windows"
    winps = root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    winps.parent.mkdir(parents=True)
    winps.write_text("")
    monkeypatch.setenv("SystemRoot", str(root))
    return str(winps)


def _pwsh_in(prefix: Path) -> Path:
    exe = prefix / "lib" / "powershell" / "pwsh.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    return exe


def _script(tmp_path: Path, text: str) -> Path:
    script = tmp_path / "build.ps1"
    script.write_text(text)
    return script


def test_finds_the_host_tool_in_the_build_prefix(tmp_path, windows):
    """--with-deps builds the declared powershell host tool into <prefix>.build."""
    exe = _pwsh_in(tmp_path / "prefix.build")
    assert builder._find_pwsh(tmp_path / "prefix", tmp_path / "prefix.build") == str(exe)


def test_build_prefix_wins_over_prefix(tmp_path, windows):
    _pwsh_in(tmp_path / "prefix")
    exe = _pwsh_in(tmp_path / "prefix.build")
    assert builder._find_pwsh(tmp_path / "prefix", tmp_path / "prefix.build") == str(exe)


def test_finds_an_installed_pwsh_in_the_prefix(tmp_path, windows):
    exe = _pwsh_in(tmp_path / "prefix")
    assert builder._find_pwsh(tmp_path / "prefix", tmp_path / "prefix.build") == str(exe)


def test_pwsh_on_path_wins_over_windows_powershell(tmp_path, windows, monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda name: r"C:\pwsh\pwsh.exe")
    script = _script(tmp_path, "#Requires -Version 5.1\n")
    assert builder._find_pwsh(tmp_path / "prefix", script=script) == r"C:\pwsh\pwsh.exe"


@pytest.mark.parametrize(
    "header",
    [
        "#Requires -Version 5.1\n",
        "# a comment first\n#requires -version 5\n",
        "#REQUIRES -Version 3.0\n",
        "\n\n  #Requires -Version 5.1.0\n",
    ],
)
def test_a_script_that_declares_5_1_runs_under_windows_powershell(tmp_path, windows, header):
    script = _script(tmp_path, header + "$ErrorActionPreference = 'Stop'\n")
    assert builder._find_pwsh(tmp_path / "prefix", script=script) == windows


@pytest.mark.parametrize(
    "header",
    [
        "$ErrorActionPreference = 'Stop'\n",  # no declaration: cvcpkg's own scripts need pwsh 7
        "#Requires -Version 7.0\n",
        "#Requires -Version 5.2\n",
        "#Requires -Version 5.1\n#Requires -PSEdition Core\n",
        "#Requires -Version 5.1 -PSEdition Core\n",
        "# #Requires -Version 5.1 is only mentioned here\n",
    ],
)
def test_other_scripts_still_need_pwsh_7(tmp_path, windows, header):
    script = _script(tmp_path, header)
    with pytest.raises(builder.BuildError, match=r"pwsh 7 not found.*#Requires -Version 5\.1"):
        builder._find_pwsh(tmp_path / "prefix", script=script)


def test_no_windows_powershell_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(builder.sys, "platform", "linux")
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    script = _script(tmp_path, "#Requires -Version 5.1\n")
    with pytest.raises(builder.BuildError, match=r"pwsh 7 not found") as e:
        builder._find_pwsh(tmp_path / "prefix", tmp_path / "prefix.build", script=script)
    assert "#Requires" not in str(e.value)


def test_posix_pwsh_in_the_build_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(builder.sys, "platform", "linux")
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    exe = tmp_path / "prefix.build" / "bin" / "pwsh"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert builder._find_pwsh(tmp_path / "prefix", tmp_path / "prefix.build") == str(exe)


def test_windows_powershell_runs_with_execution_policy_bypass(tmp_path, windows):
    """Client Windows defaults Windows PowerShell to Restricted, which refuses -File."""
    script = _script(tmp_path, "#Requires -Version 5.1\n")
    assert builder._pwsh_command(script, tmp_path / "prefix") == [
        windows,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def test_pwsh_7_command_is_unchanged(tmp_path, windows):
    exe = _pwsh_in(tmp_path / "prefix")
    script = _script(tmp_path, "#Requires -Version 5.1\n")
    assert builder._pwsh_command(script, tmp_path / "prefix") == [
        str(exe),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
    ]


def test_the_powershell_recipe_can_bootstrap_itself():
    """Its build.ps1 is what puts pwsh 7 on a host that has none, so it must
    keep declaring (and keep to) Windows PowerShell 5.1."""
    assert builder._runs_on_windows_powershell(RECIPES / "powershell" / "build.ps1")
