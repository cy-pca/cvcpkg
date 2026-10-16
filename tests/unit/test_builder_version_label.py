"""A builder labels itself with the code it runs: cvcpkg=<version>[@<commit>]."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from cvcpkg import __version__
from cvcpkg.cli import _builder


def test_without_a_checkout_the_label_is_the_version(monkeypatch):
    monkeypatch.setattr(_builder, "_find_update_checkout", lambda: None)
    assert _builder._version_label() == f"cvcpkg={__version__}"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_with_a_checkout_the_label_carries_its_head(tmp_path, monkeypatch):
    repo = tmp_path / "cvcpkg"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[tool.poetry]\nname = "cvcpkg"\nversion = "9.9.9"\n')
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, check=True, capture_output=True, text=True, env={**env, **_env()}
    )
    run("init", "-q")
    run("add", "pyproject.toml")
    run("commit", "-q", "-m", "x")
    head = run("rev-parse", "--short=10", "HEAD").stdout.strip()
    monkeypatch.setattr(_builder, "_find_update_checkout", lambda: repo)
    assert _builder._version_label() == f"cvcpkg={__version__}@{head}"


def _env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
