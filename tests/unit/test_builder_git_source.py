# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Tests for `source.type: git` — the fetch path Mitsuba/Dr.Jit need.

Everything here runs against real repositories created in tmp_path, so the
tests exercise actual git behaviour rather than a mock of it, and never touch
the network.  The one thing deliberately NOT covered is live submodule
fetching: submodule URLs in a real recipe are absolute https upstreams, and
recent git refuses `file://` submodules by default, so a local fixture would
be testing git's security policy rather than cvcpkg.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from cvcpkg.builder import (
    RecipeError,
    SourceSpec,
    _git_offline,
    _mirror_path,
    _normalize_submodules,
    _rmtree_force,
    _tree_digest,
    fetch_source,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

ZERO = "0" * 40


def _git(args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def upstream(tmp_path):
    """A tiny real repo with two commits; returns (path, head_sha, first_sha)."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    (repo / "README.md").write_text("one\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-qm", "one"], repo)
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    (repo / "README.md").write_text("two\n", encoding="utf-8")
    _git(["commit", "-qam", "two"], repo)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    return repo, head, first


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "gitcache"
    monkeypatch.setenv("CVCPKG_GIT_CACHE", str(d))
    monkeypatch.delenv("CVCPKG_GIT_OFFLINE", raising=False)
    return d


def _recipe(source: SourceSpec):
    class _R:
        pass

    r = _R()
    r.source = source
    return r


def _fetch(source, work):
    work.mkdir(parents=True, exist_ok=True)
    return fetch_source(_recipe(source), work)


# ── validation ───────────────────────────────────────────────────


def test_commit_is_required(tmp_path, cache):
    src = SourceSpec(type="git", url="https://example.invalid/x.git")
    with pytest.raises(RecipeError, match="mutable ref"):
        _fetch(src, tmp_path / "w")


def test_url_is_required(tmp_path, cache):
    src = SourceSpec(type="git", commit="a" * 40)
    with pytest.raises(RecipeError, match="no URL"):
        _fetch(src, tmp_path / "w")


def test_short_commit_is_rejected(tmp_path, cache):
    src = SourceSpec(type="git", url="https://example.invalid/x.git", commit="abc123")
    with pytest.raises(RecipeError, match="40-character"):
        _fetch(src, tmp_path / "w")


# ── fetching ─────────────────────────────────────────────────────


def test_checks_out_the_pinned_commit_not_the_tip(tmp_path, cache, upstream):
    repo, head, first = upstream
    # Pin the FIRST commit while the branch tip is the second one: a fetch that
    # quietly took the tip would read "two".
    src = SourceSpec(type="git", url=str(repo), commit=first)
    out = _fetch(src, tmp_path / "w")
    assert (out / "README.md").read_text(encoding="utf-8") == "one\n"
    assert head != first


def test_a_moved_tag_does_not_change_what_is_built(tmp_path, cache, upstream):
    repo, head, first = upstream
    _git(["tag", "-f", "v1", first], repo)
    src = SourceSpec(type="git", url=str(repo), commit=first)
    out = _fetch(src, tmp_path / "w1")
    assert (out / "README.md").read_text(encoding="utf-8") == "one\n"

    # Move the tag to the other commit; the pin must still win.
    _git(["tag", "-f", "v1", head], repo)
    out2 = _fetch(src, tmp_path / "w2")
    assert (out2 / "README.md").read_text(encoding="utf-8") == "one\n"


def test_unknown_commit_fails_loudly(tmp_path, cache, upstream):
    repo, _head, _first = upstream
    src = SourceSpec(type="git", url=str(repo), commit=ZERO)
    with pytest.raises(RecipeError, match="not present"):
        _fetch(src, tmp_path / "w")


def test_digest_is_written_and_stable_across_cold_runs(tmp_path, cache, upstream):
    repo, head, _first = upstream
    src = SourceSpec(type="git", url=str(repo), commit=head)

    w1 = tmp_path / "w1"
    _fetch(src, w1)
    d1 = (w1 / "git-tree-digest.txt").read_text(encoding="utf-8").strip()

    # Cold cache: wipe the mirror so the second run re-clones from scratch.
    # _rmtree_force, not shutil.rmtree: git marks its pack files read-only and
    # a plain rmtree leaves the directory half-deleted on Windows, which is a
    # warm-ish cache pretending to be a cold one.
    _rmtree_force(cache)
    w2 = tmp_path / "w2"
    _fetch(src, w2)
    d2 = (w2 / "git-tree-digest.txt").read_text(encoding="utf-8").strip()

    assert d1 == d2
    assert len(d1) == 64


def test_git_metadata_is_kept(tmp_path, cache, upstream):
    # Mitsuba and friends run `git describe` at configure time.
    repo, head, _first = upstream
    src = SourceSpec(type="git", url=str(repo), commit=head)
    out = _fetch(src, tmp_path / "w")
    assert (out / ".git").exists()


# ── offline mode ─────────────────────────────────────────────────


def test_offline_with_cold_cache_names_the_missing_mirror(tmp_path, cache, monkeypatch, upstream):
    repo, head, _first = upstream
    monkeypatch.setenv("CVCPKG_GIT_OFFLINE", "1")
    src = SourceSpec(type="git", url=str(repo), commit=head)
    with pytest.raises(RecipeError) as ei:
        _fetch(src, tmp_path / "w")
    msg = str(ei.value)
    assert "CVCPKG_GIT_OFFLINE" in msg
    assert str(_mirror_path(cache, str(repo))) in msg, "error must name the mirror it wanted"


def test_offline_with_warm_cache_succeeds(tmp_path, cache, monkeypatch, upstream):
    repo, head, _first = upstream
    src = SourceSpec(type="git", url=str(repo), commit=head)
    _fetch(src, tmp_path / "warm")  # online: populates the mirror

    monkeypatch.setenv("CVCPKG_GIT_OFFLINE", "1")
    out = _fetch(src, tmp_path / "w2")
    assert (out / "README.md").is_file()


def test_offline_rejects_a_disabled_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CVCPKG_GIT_CACHE", "")
    monkeypatch.setenv("CVCPKG_GIT_OFFLINE", "1")
    src = SourceSpec(type="git", url="https://example.invalid/x.git", commit="a" * 40)
    with pytest.raises(RecipeError, match="nothing to build from offline"):
        _fetch(src, tmp_path / "w")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
    ],
)
def test_offline_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("CVCPKG_GIT_OFFLINE", value)
    assert _git_offline() is expected


# ── helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "none"),
        (True, "recursive"),
        (False, "none"),
        ("none", "none"),
        ("shallow", "shallow"),
        ("RECURSIVE", "recursive"),
    ],
)
def test_normalize_submodules(raw, expected):
    assert _normalize_submodules(raw) == expected


def test_normalize_submodules_rejects_nonsense():
    with pytest.raises(RecipeError, match="none/shallow/recursive"):
        _normalize_submodules("sometimes")


def test_tree_digest_depends_on_submodule_state():
    base = _tree_digest("a" * 40, [])
    assert base != _tree_digest("b" * 40, [])
    assert base != _tree_digest("a" * 40, [("ext/x", "c" * 40)])
    # Order must not matter to the caller; the digest sorts its input.
    two = [("ext/a", "1" * 40), ("ext/b", "2" * 40)]
    assert _tree_digest("a" * 40, two) == _tree_digest("a" * 40, list(reversed(sorted(two))))


def test_mirror_path_is_readable_and_unique():
    from pathlib import Path

    c = Path("/cache")
    a = _mirror_path(c, "https://github.com/mitsuba-renderer/drjit.git")
    b = _mirror_path(c, "https://github.com/mitsuba-renderer/drjit-core.git")
    assert a != b
    assert a.name.startswith("drjit-")
    assert a.name.endswith(".git")
    # Same URL is stable across calls, which is what makes the cache a cache.
    assert a == _mirror_path(c, "https://github.com/mitsuba-renderer/drjit.git")
