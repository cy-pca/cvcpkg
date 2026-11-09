"""Tests for scripts/changed_recipes.py: which recipes a deploy has to push."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("yaml")

if shutil.which("git") is None:  # pragma: no cover - CI and dev hosts have git
    pytest.skip("git is required", allow_module_level=True)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "changed_recipes.py"


def _load():
    spec = importlib.util.spec_from_file_location("changed_recipes", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


mod = _load()

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **_ENV, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    out = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _recipe(repo: Path, name: str, extra: str = "") -> None:
    _write(repo, f"recipes/{name}/recipe.yaml", f"recipe:\n  name: {name}\n{extra}")
    _write(repo, f"recipes/{name}/build.sh", "#!/bin/sh\n")


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _recipe(r, "zlib")
    _recipe(r, "openssl")
    _recipe(r, "levmar", "source:\n  type: vendored\n  path: third-party/levmar\n")
    _write(r, "recipes/_common/env.sh")
    _write(r, "third-party/levmar/lm.c")
    _write(r, "third-party/other/o.c")
    _write(r, "src/app.py")
    _commit(r, "base")
    return r


def _plan(repo: Path, old: str):
    new = _git(repo, "rev-parse", "HEAD")
    return mod.plan(old, new, repo, repo / "recipes")


def test_an_edit_to_one_recipe_selects_only_that_recipe(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "recipes/zlib/build.sh", "#!/bin/sh\necho changed\n")
    _write(repo, "src/app.py", "y\n")  # outside every bundle
    _commit(repo, "edit zlib")
    assert _plan(repo, base)[0] == ["zlib"]


def test_a_new_recipe_and_a_patch_file_are_selected_sorted(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _recipe(repo, "brand-new")
    _write(repo, "recipes/openssl/patches/fix.patch")
    _commit(repo, "add")
    assert _plan(repo, base)[0] == ["brand-new", "openssl"]


def test_a_change_under_common_means_every_recipe(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "recipes/_common/env.sh", "changed\n")
    _commit(repo, "common")
    names, why = _plan(repo, base)
    assert names == mod.FULL
    assert "_common" in why


def test_a_new_file_under_common_means_every_recipe(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "recipes/_common/sub/new.sh")
    _commit(repo, "common")
    assert _plan(repo, base)[0] == mod.FULL


def test_vendored_source_changes_select_the_recipe_that_bundles_it(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "third-party/levmar/lm.c", "new\n")
    _write(repo, "third-party/other/o.c", "new\n")  # nobody vendors this
    _commit(repo, "vendored")
    assert _plan(repo, base)[0] == ["levmar"]


def test_a_tree_next_to_the_vendored_one_is_not_a_prefix_match(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "third-party/levmar-extra/x.c")
    _commit(repo, "sibling")
    assert _plan(repo, base)[0] == []


def test_a_deleted_recipe_is_left_out(repo):
    base = _git(repo, "rev-parse", "HEAD")
    shutil.rmtree(repo / "recipes" / "zlib")
    _write(repo, "recipes/openssl/build.sh", "changed\n")
    _commit(repo, "drop zlib")
    assert _plan(repo, base)[0] == ["openssl"]


def test_a_renamed_recipe_directory_selects_the_new_name(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "recipes/zlib", "recipes/zlib-ng")
    _commit(repo, "rename")
    assert _plan(repo, base)[0] == ["zlib-ng"]


def test_directories_push_all_skips_are_never_selected(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "recipes/_scratch/recipe.yaml")
    _write(repo, "recipes/.hidden/recipe.yaml")
    _write(repo, "recipes/no-recipe-yaml/build.sh")
    _write(repo, "recipes/README.md")
    _commit(repo, "noise")
    assert _plan(repo, base)[0] == []


def test_nothing_changed_selects_nothing(repo):
    head = _git(repo, "rev-parse", "HEAD")
    names, _ = _plan(repo, head)
    assert names == []


@pytest.mark.parametrize(
    "old",
    [
        "",
        "0" * 40,
        "not-a-sha",
        "--output=/tmp/x",
        "deadbeefdeadbeef",  # well formed, but not a commit in this checkout
    ],
)
def test_an_unusable_base_commit_means_every_recipe(repo, old):
    _write(repo, "recipes/zlib/build.sh", "changed\n")
    _commit(repo, "edit")
    names, why = _plan(repo, old)
    assert names == mod.FULL
    assert why


def test_a_new_commit_that_is_not_a_sha_means_every_recipe(repo):
    head = _git(repo, "rev-parse", "HEAD")
    assert mod.plan(head, "HEAD", repo, repo / "recipes")[0] == mod.FULL


def test_cli_prints_names_one_per_line_and_the_reason_on_stderr(repo, capsys):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "recipes/zlib/build.sh", "changed\n")
    _write(repo, "recipes/openssl/build.sh", "changed\n")
    new = _commit(repo, "edit two")
    assert mod.main([base, new, "--repo", str(repo)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == ["openssl", "zlib"]
    assert "2 recipe(s)" in out.err


def test_cli_prints_a_lone_star_for_a_full_push(repo, capsys):
    new = _git(repo, "rev-parse", "HEAD")
    assert mod.main(["", new, "--repo", str(repo)]) == 0
    out = capsys.readouterr()
    assert out.out == "*\n"
    assert "push every recipe" in out.err


def test_cli_prints_nothing_when_nothing_changed(repo, capsys):
    head = _git(repo, "rev-parse", "HEAD")
    assert mod.main([head, head, "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == ""


def test_a_recipes_dir_outside_the_repo_means_every_recipe(repo, tmp_path):
    head = _git(repo, "rev-parse", "HEAD")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "zlib").mkdir(parents=True)
    (elsewhere / "zlib" / "recipe.yaml").write_text("recipe: {}\n")
    assert mod.plan(head, head, repo, elsewhere)[0] == mod.FULL


def test_this_repos_real_history_is_selectable():
    """The checkout under test: HEAD against itself selects nothing, and a base
    that does not exist selects everything."""
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():  # sdist / wheel test runs
        pytest.skip("not a git checkout")
    try:
        head = _git(root, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        pytest.skip("git cannot read this checkout")
    assert mod.plan(head, head, root, root / "recipes")[0] == []
    assert mod.plan("f" * 40, head, root, root / "recipes")[0] == mod.FULL
