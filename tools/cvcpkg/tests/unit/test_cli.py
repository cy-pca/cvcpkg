"""Tests for cvcpkg.cli — smoke tests and new command coverage."""

import os

import pytest

from cvcpkg.cli import main


def test_help_returns_zero():
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0


def test_version(capsys):
    try:
        main(["--version"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "cvcpkg" in captured.out


def test_install_no_components(capsys):
    ret = main(["install"])
    captured = capsys.readouterr()
    assert "nothing to do" in captured.out.lower() or ret == 0


def test_validate_components():
    """Validate components.yaml from the repo."""
    if os.path.exists("packaging/components.yaml"):
        ret = main(["validate", "components"])
        assert ret == 0


# ── CLI subcommand help ─────────────────────────────────────────

@pytest.mark.parametrize("subcmd", [
    "install", "list", "validate", "verify", "gc",
    "build", "pack", "recipes",
])
def test_subcommand_help(subcmd):
    with pytest.raises(SystemExit) as exc_info:
        main([subcmd, "--help"])
    assert exc_info.value.code == 0


# ── recipes command ─────────────────────────────────────────────

def test_recipes_list(capsys):
    """cvcpkg recipes --list should print the recipe table."""
    ret = main(["recipes", "--list"])
    captured = capsys.readouterr()
    # Should list at least zlib
    assert ret == 0
    assert "zlib" in captured.out

def test_recipes_show(capsys):
    """cvcpkg recipes --show zlib should print recipe details."""
    ret = main(["recipes", "--show", "zlib"])
    captured = capsys.readouterr()
    assert ret == 0
    assert "zlib" in captured.out
    assert "Version:" in captured.out or "1.3.1" in captured.out

def test_recipes_show_not_found(capsys):
    ret = main(["recipes", "--show", "nonexistent-pkg-xyz"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out

def test_recipes_default_is_list(capsys):
    """Plain 'cvcpkg recipes' should default to --list behavior."""
    ret = main(["recipes"])
    captured = capsys.readouterr()
    assert ret == 0
    assert "zlib" in captured.out


# ── build / pack argument parsing ──────────────────────────────

def test_build_no_recipe(capsys):
    """cvcpkg build without recipe should print usage."""
    with pytest.raises(SystemExit) as exc_info:
        main(["build"])
    assert exc_info.value.code != 0

def test_pack_no_recipe(capsys):
    """cvcpkg pack without recipe should print usage."""
    with pytest.raises(SystemExit) as exc_info:
        main(["pack"])
    assert exc_info.value.code != 0


# ── unknown command ─────────────────────────────────────────────

def test_unknown_command():
    with pytest.raises(SystemExit) as exc_info:
        main(["frobnicate"])
    assert exc_info.value.code == 2  # argparse rejects invalid choices


# ── no command ──────────────────────────────────────────────────

def test_no_command(capsys):
    ret = main([])
    assert ret == 0
