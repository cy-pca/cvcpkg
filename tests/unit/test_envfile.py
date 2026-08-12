"""Secrets belong in a file with its own permissions, not in argv.

``--token`` appears on 63 options across the CLI and every one of them puts the
bearer token in the process command line, which ``/proc/<pid>/cmdline`` hands to
any local user (Task Manager does the same on Windows).  ``cvcpkg --env-file``
loads settings before Click resolves ``envvar=``, so one option serves every
existing ``--token`` site without changing them -- and, critically, WITHOUT
displacing anything an existing deployment already sets.
"""

import os
import sys

import click
import pytest
from click.testing import CliRunner

from cvcpkg.cli import cli
from cvcpkg.envfile import (
    EnvFileError,
    candidate_env_files,
    load_default_env_files,
    load_env_file,
    parse_env_file,
)


@pytest.fixture(autouse=True)
def _isolate_environ():
    """Undo every environment change these tests make.

    Loading an env file is *supposed* to mutate ``os.environ`` -- that is the
    whole mechanism -- but it does so with plain ``os.environ[k] = v``, which
    ``monkeypatch`` does not know about and therefore cannot roll back.  Without
    this, ``CVCPKG_TOKEN`` and ``CVCPKG_SERVER_URL`` escape into every later test
    in the process and quietly satisfy options that those tests expect to be
    missing.  Unit and integration runs are split into separate processes in CI,
    so that kind of leak is invisible there until the full suite runs in one.
    """
    saved = os.environ.copy()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ── parsing ─────────────────────────────────────────────────────


def test_parses_comments_export_and_blank_lines():
    parsed = parse_env_file(
        "# a comment\n"
        "\n"
        "CVCPKG_TOKEN=cvctok_plain\n"
        "export CVCPKG_SERVER_URL=https://cvcpkg.org\n"
        "   # indented comment\n"
    )
    assert parsed == {
        "CVCPKG_TOKEN": "cvctok_plain",
        "CVCPKG_SERVER_URL": "https://cvcpkg.org",
    }


def test_quoting_and_escapes():
    parsed = parse_env_file(
        'DOUBLE="a b"\n'
        "SINGLE='c d'\n"
        'ESCAPED="line1\\nline2"\n'
        "LITERAL='back\\slash'\n"
        "BARE=no_quotes\n"
    )
    assert parsed["DOUBLE"] == "a b"
    assert parsed["SINGLE"] == "c d"
    assert parsed["ESCAPED"] == "line1\nline2"
    # Single quotes are literal throughout: a token full of backslashes needs
    # no thought from whoever writes the file.
    assert parsed["LITERAL"] == "back\\slash"
    assert parsed["BARE"] == "no_quotes"


def test_no_shell_expansion():
    # A secrets file must not execute anything, and a token containing '$'
    # has to survive verbatim.
    parsed = parse_env_file("HOME_REF=$HOME\nSUB=$(id -u)\nTOK=abc$def\n")
    assert parsed["HOME_REF"] == "$HOME"
    assert parsed["SUB"] == "$(id -u)"
    assert parsed["TOK"] == "abc$def"


def test_trailing_comment_only_after_space():
    parsed = parse_env_file("A=value # trailing\nB=has#hash\n")
    assert parsed["A"] == "value"
    # No preceding space -> part of the value.  Real tokens contain '#'.
    assert parsed["B"] == "has#hash"


def test_last_assignment_wins():
    assert parse_env_file("K=first\nK=second\n")["K"] == "second"


@pytest.mark.parametrize(
    "text",
    ["NOEQUALS\n", "1BAD=x\n", "has space=x\n", 'UNTERMINATED="oops\n'],
)
def test_malformed_lines_rejected(text):
    with pytest.raises(EnvFileError):
        parse_env_file(text)


# ── precedence ──────────────────────────────────────────────────


def test_real_env_wins_over_file(tmp_path, monkeypatch):
    """The compatibility guarantee: adding a file cannot change what an
    existing deployment already resolves to."""
    env = tmp_path / "cvcpkg.env"
    env.write_text("CVCPKG_TOKEN=from_file\nCVCPKG_SERVER_URL=from_file\n")
    monkeypatch.setenv("CVCPKG_TOKEN", "from_real_env")
    monkeypatch.delenv("CVCPKG_SERVER_URL", raising=False)

    applied = load_env_file(env)

    assert os.environ["CVCPKG_TOKEN"] == "from_real_env"  # untouched
    assert os.environ["CVCPKG_SERVER_URL"] == "from_file"  # filled the gap
    assert applied == ["CVCPKG_SERVER_URL"]


def test_override_inverts_precedence(tmp_path, monkeypatch):
    env = tmp_path / "cvcpkg.env"
    env.write_text("CVCPKG_TOKEN=from_file\n")
    monkeypatch.setenv("CVCPKG_TOKEN", "from_real_env")

    load_env_file(env, override=True)

    assert os.environ["CVCPKG_TOKEN"] == "from_file"


def test_missing_file_raises_unless_optional(tmp_path):
    missing = tmp_path / "nope.env"
    with pytest.raises(EnvFileError):
        load_env_file(missing)
    assert load_env_file(missing, required=False) == []


# ── default search path ─────────────────────────────────────────


def test_project_local_file_shadows_system(tmp_path, monkeypatch):
    """More specific files win, and neither has to know the other exists."""
    home = tmp_path / "home"
    (home / ".config" / "cvcpkg").mkdir(parents=True)
    (home / ".config" / "cvcpkg" / "env").write_text("CVCPKG_TOKEN=user\nONLY_USER=yes\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".cvcpkg.env").write_text("CVCPKG_TOKEN=project\n")

    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CVCPKG_ENV_FILE", raising=False)
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    monkeypatch.delenv("ONLY_USER", raising=False)

    load_default_env_files()

    assert os.environ["CVCPKG_TOKEN"] == "project"  # project shadows user
    assert os.environ["ONLY_USER"] == "yes"  # but user file still contributes


def test_explicit_env_var_is_searched_first(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.env"
    explicit.write_text("CVCPKG_TOKEN=explicit\n")
    monkeypatch.setenv("CVCPKG_ENV_FILE", str(explicit))
    assert candidate_env_files()[0] == explicit


def test_malformed_default_file_is_skipped_not_fatal(tmp_path, monkeypatch, capsys):
    """A broken file must not take down an unrelated command -- the option that
    needed the value fails with its own, clearer error instead."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".cvcpkg.env").write_text("THIS IS NOT AN ENV FILE\n")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.delenv("CVCPKG_ENV_FILE", raising=False)

    assert load_default_env_files() == []
    assert "WARNING" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_world_readable_file_warns(tmp_path, monkeypatch, capsys):
    env = tmp_path / "loose.env"
    env.write_text("CVCPKG_TOKEN=secret\n")
    env.chmod(0o644)
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)

    load_env_file(env)

    err = capsys.readouterr().err
    assert "WARNING" in err and "chmod 600" in err
    # Advisory only: shared builders and CI images must still work.
    assert os.environ["CVCPKG_TOKEN"] == "secret"


# ── CLI integration ─────────────────────────────────────────────


def test_env_file_feeds_subcommand_without_touching_argv(tmp_path, monkeypatch):
    """The point of the feature: a subcommand's required --token resolves from
    the file, so the secret never appears in the command line."""
    env = tmp_path / "cvcpkg.env"
    env.write_text("CVCPKG_TOKEN=cvctok_from_file\nCVCPKG_SERVER_URL=https://example.invalid\n")
    monkeypatch.delenv("CVCPKG_TOKEN", raising=False)
    monkeypatch.delenv("CVCPKG_SERVER_URL", raising=False)
    monkeypatch.delenv("CVCPKG_ENV_FILE", raising=False)

    seen = {}

    @click.command("envfile-probe")
    def _probe():
        seen["token"] = os.environ.get("CVCPKG_TOKEN")
        seen["server"] = os.environ.get("CVCPKG_SERVER_URL")

    # Registered on the shared root group, so remove it again -- a stray command
    # would leak into every later test in this process.
    cli.add_command(_probe)
    try:
        result = CliRunner().invoke(cli, ["--env-file", str(env), "envfile-probe"])
    finally:
        cli.commands.pop("envfile-probe", None)

    assert result.exit_code == 0, result.output
    assert seen == {"token": "cvctok_from_file", "server": "https://example.invalid"}


def test_explicit_env_file_must_exist(tmp_path, monkeypatch):
    """A typo'd --env-file has to fail loudly: failing open would surface as a
    confusing 'missing token' from whatever ran next."""
    monkeypatch.delenv("CVCPKG_ENV_FILE", raising=False)
    result = CliRunner().invoke(cli, ["--env-file", str(tmp_path / "typo.env"), "--help"])
    assert result.exit_code != 0
    assert "not found" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell quoting")
def test_env_file_is_shell_sourceable(tmp_path):
    """`export`-prefixed lines mean the same file can be sourced by the shell
    wrappers that already exist on the builders."""
    import subprocess

    env = tmp_path / "cvcpkg.env"
    env.write_text("export CVCPKG_TOKEN=cvctok_sourceable\n")
    out = subprocess.run(
        ["sh", "-c", f'. "{env}" && printf %s "$CVCPKG_TOKEN"'],
        capture_output=True,
        text=True,
    )
    assert out.stdout == "cvctok_sourceable"
    assert parse_env_file(env.read_text())["CVCPKG_TOKEN"] == "cvctok_sourceable"
