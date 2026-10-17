"""The ``cvcpkg`` console script goes through :func:`cvcpkg.cli.main`.

It used to be ``cvcpkg.cli:cli``, the bare Click group, so a pip-installed
cvcpkg printed a Python traceback for every CvcpkgError (a ResolveError from
``install`` on netbsd-build-2), while the frozen binary, whose ``__main__``
calls ``main()``, printed one ``cvcpkg: ERROR:`` line.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from cvcpkg.cli import cli, main
from cvcpkg.errors import ResolveError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def raising_command():
    """Register a throwaway subcommand that raises what the test hands it."""
    box: dict[str, BaseException] = {}

    @cli.command("zz-test-raise", hidden=True)
    def _raise() -> None:
        raise box["exc"]

    yield box
    cli.commands.pop("zz-test-raise", None)


def test_console_script_is_main():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'cvcpkg = "cvcpkg.cli:main"' in text


def test_cvcpkg_error_is_one_line_exit_1(raising_command, capsys):
    raising_command["exc"] = ResolveError("cannot satisfy requirements:\n  no candidate for 'x'")
    assert main(["zz-test-raise"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("cvcpkg: ERROR: cannot satisfy requirements:")
    assert "Traceback" not in err


def test_usage_error_keeps_clicks_exit_code(capsys):
    assert main(["install", "--no-such-flag"]) == 2
    assert "No such option" in capsys.readouterr().err


def test_abort_prints_aborted(raising_command, capsys):
    raising_command["exc"] = click.exceptions.Abort()
    assert main(["zz-test-raise"]) == 1
    assert "Aborted!" in capsys.readouterr().err


def test_help_exits_0(capsys):
    assert main(["--help"]) == 0
    assert "Usage:" in capsys.readouterr().out
