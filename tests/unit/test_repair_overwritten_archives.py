"""scripts/repair_overwritten_archives.py: which rows it retires, and how.

The shapes are dev's, from 2026-10-31: a live local ``none/none`` row whose
``...-release-shared`` file populate or /v1/publish overwrote, next to rows
that share a file with the same bytes (which #552 relies on and must survive).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = "flit-1.0-any-noarch-release-shared.tar.zst"


@pytest.fixture
def script():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import repair_overwritten_archives

        yield repair_overwritten_archives
    finally:
        sys.path.remove(str(REPO / "scripts"))


def _row(name="flit", build_type="none", link="none", sha="a", *, file=SHARED, **kw):
    r = {
        "name": name,
        "version": "1.0",
        "platform": "any",
        "arch": "noarch",
        "build_type": build_type,
        "link": link,
        "sha256": sha,
        "archive_url": f"/v1/download/{file}" if file else "",
        "yanked": False,
        "org": "",
        "published_by": "dev-builder",
    }
    r.update(kw)
    return r


def test_overwritten_row_is_the_target(script):
    local = _row(sha="local")
    mirrored = _row(build_type="release", link="shared", sha="up", yanked=True)
    targets, unclaimed = script.select_targets(
        script.shared_files([local, mirrored]), {SHARED: "up"}
    )
    assert [(t.row, t.holder, t.file) for t in targets] == [(local, mirrored, SHARED)]
    assert unclaimed == []


def test_rows_sharing_the_files_bytes_are_left_alone(script):
    rows = [_row(sha="up", yanked=True), _row(build_type="debug", link="shared", sha="up")]
    assert script.select_targets(script.shared_files(rows), {SHARED: "up"}) == ([], [])


def test_bytes_no_row_claims_are_reported_not_retired(script):
    rows = [_row(sha="a"), _row(build_type="release", link="shared", sha="b")]
    assert script.select_targets(script.shared_files(rows), {SHARED: "c"}) == ([], [SHARED])


def test_unhashed_file_yields_nothing(script):
    rows = [_row(sha="a"), _row(build_type="release", link="shared", sha="b")]
    assert script.select_targets(script.shared_files(rows), {SHARED: ""}) == ([], [])


def test_only_files_two_rows_store_are_shared(script):
    rows = [_row(), _row(name="other", file="other.tar.zst"), _row(name="ph", file="")]
    assert list(script.shared_files(rows)) == []


def test_retire_stops_before_the_nuke_when_the_yank_hits_more(script, monkeypatch):
    calls = []

    def post(server, token, row, action):
        calls.append(action)
        return 2  # an org twin at the same scope

    monkeypatch.setattr(script, "post_scoped", post)
    with pytest.raises(SystemExit, match="changed 2 row"):
        script.retire("http://dev", "tok", _row())
    assert calls == ["yank"]


def test_retire_yanks_then_nukes_exactly_one(script, monkeypatch):
    calls = []
    monkeypatch.setattr(script, "post_scoped", lambda s, t, row, action: calls.append(action) or 1)
    script.retire("http://dev", "tok", _row())
    assert calls == ["yank", "nuke"]


def _wire(script, monkeypatch, rows, file_sha):
    monkeypatch.setattr(script, "fetch_catalog", lambda server, token: rows)
    monkeypatch.setattr(script, "hash_archive", lambda server, token, f: file_sha[f])
    posted = []
    monkeypatch.setattr(script, "post_scoped", lambda *a: posted.append(a) or 1)
    return posted


def test_dry_run_lists_and_writes_nothing(script, monkeypatch, capsys):
    rows = [_row(sha="local"), _row(build_type="release", link="shared", sha="up", yanked=True)]
    posted = _wire(script, monkeypatch, rows, {SHARED: "up"})
    assert script.main(["--server", "http://dev"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN: 1 row(s) would be retired" in out
    assert "flit==1.0 any/noarch/none/none  file holds flit==1.0 any/noarch/release/shared" in out
    assert posted == []


def test_apply_refuses_a_changed_target_set(script, monkeypatch):
    rows = [_row(sha="local"), _row(build_type="release", link="shared", sha="up", yanked=True)]
    posted = _wire(script, monkeypatch, rows, {SHARED: "up"})
    monkeypatch.setenv("CVCPKG_TOKEN", "tok")
    with pytest.raises(SystemExit, match="not the expected 2"):
        script.main(["--server", "http://dev", "--apply", "--expect", "2"])
    assert posted == []


def test_an_org_twin_makes_a_target_unsafe(script, monkeypatch, capsys):
    rows = [
        _row(sha="local"),
        _row(build_type="release", link="shared", sha="up", yanked=True),
        _row(sha="org", org="acme", file="acme.tar.zst"),
    ]
    _wire(script, monkeypatch, rows, {SHARED: "up"})
    assert script.main(["--server", "http://dev"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED flit==1.0 any/noarch/none/none" in out
    assert "DRY RUN: 0 row(s)" in out


def test_apply_lands_each_batch_after_a_sync_and_checks_the_reimports(script, monkeypatch, capsys):
    rows, file_sha = [], {}
    for n in ("a", "b", "c"):
        f = f"{n}-1.0-any-noarch-release-shared.tar.zst"
        rows += [
            _row(name=n, sha="local", file=f),
            _row(name=n, build_type="release", link="shared", sha="up", file=f, yanked=True),
        ]
        file_sha[f] = "up"
    reimported = [_row(name=n, sha="up", file=f"{n}-none.tar.zst") for n in "abc"]
    events = []

    def last_sync(server):
        # Every look finds one more sync finished, 15 minutes after the last.
        n = events.count("sync?")
        events.append("sync?")
        return f"2026-11-06T{n // 4:02d}:{n % 4 * 15:02d}:00+00:00"

    def catalog(server, token):
        if server == "https://up":
            return [_row(name=n, sha="up") for n in "abc"]
        return reimported if "nuke" in events else rows

    monkeypatch.setattr(script, "fetch_catalog", catalog)
    monkeypatch.setattr(script, "hash_archive", lambda server, token, f: file_sha[f])
    monkeypatch.setattr(script, "last_sync", last_sync)
    monkeypatch.setattr(script.time, "sleep", lambda s: None)
    monkeypatch.setattr(script, "post_scoped", lambda s, t, row, action: events.append(action) or 1)
    monkeypatch.setenv("CVCPKG_TOKEN", "tok")
    argv = ["--server", "http://dev", "--upstream", "https://up", "--apply", "--expect", "3"]
    assert script.main([*argv, "--batch", "2"]) == 0
    # batch 1 (two rows), a sync, batch 2 (one row), a sync, then the check
    assert events == ["sync?", *["yank", "nuke"] * 2, "sync?", "yank", "nuke", "sync?"]
    assert "DONE: retired 3; 3/3 re-imported" in capsys.readouterr().out
