"""`cvcpkg recipe sync-common` — refresh a work tree's vendored _common.

A local recipes tree vendors a snapshot of recipes/_common at onboarding
(new-client-project.sh) and nothing refreshes it, yet that copy is what local
builds source and what winhost stages to the Windows host.  Helpers added to
cvcpkg later are therefore silently absent — which is how an air-gapped chain
build failed ~47 minutes in with "Build script for <x>-src exited with code 1"
because stage-source.sh (added in #259) was missing from a tree onboarded
before it existed.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cvcpkg.cli import cli


def _fake_bundled(tmp_path, files: dict[str, str]) -> Path:
    """A stand-in for the installed cvcpkg's recipes/ dir."""
    recipes = tmp_path / "bundled" / "recipes"
    common = recipes / "_common"
    common.mkdir(parents=True)
    for name, body in files.items():
        p = common / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return recipes


def _work_tree(tmp_path, files: dict[str, str]) -> Path:
    """A client work tree's recipes/ dir with a vendored _common snapshot."""
    recipes = tmp_path / "work" / "recipes"
    common = recipes / "_common"
    common.mkdir(parents=True)
    for name, body in files.items():
        (common / name).write_text(body)
    return recipes


def _run(monkeypatch, bundled: Path, args):
    monkeypatch.setattr("cvcpkg.builder.find_recipes_dir", lambda: bundled)
    return CliRunner().invoke(cli, ["recipe", "sync-common", *args])


class TestRecipeSyncCommon:
    def test_adds_helper_missing_from_a_stale_work_tree(self, tmp_path, monkeypatch):
        # THE real scenario: the work tree predates stage-source.sh (#259).
        bundled = _fake_bundled(
            tmp_path,
            {"env-linux.sh": "old\n", "stage-source.sh": "cvc_stage_source() { :; }\n"},
        )
        work = _work_tree(tmp_path, {"env-linux.sh": "old\n"})
        assert not (work / "_common" / "stage-source.sh").exists()

        res = _run(monkeypatch, bundled, [str(work)])
        assert res.exit_code == 0, res.output
        assert (work / "_common" / "stage-source.sh").is_file()
        assert "stage-source.sh" in res.output
        assert "added" in res.output

    def test_updates_a_drifted_helper(self, tmp_path, monkeypatch):
        bundled = _fake_bundled(tmp_path, {"env-linux.sh": "NEW CONTENT\n"})
        work = _work_tree(tmp_path, {"env-linux.sh": "stale content\n"})
        res = _run(monkeypatch, bundled, [str(work)])
        assert res.exit_code == 0, res.output
        assert (work / "_common" / "env-linux.sh").read_text() == "NEW CONTENT\n"
        assert "updated" in res.output

    def test_already_current_is_a_noop(self, tmp_path, monkeypatch):
        bundled = _fake_bundled(tmp_path, {"env-linux.sh": "same\n"})
        work = _work_tree(tmp_path, {"env-linux.sh": "same\n"})
        res = _run(monkeypatch, bundled, [str(work)])
        assert res.exit_code == 0, res.output
        assert "already up to date" in res.output

    def test_dry_run_reports_without_writing(self, tmp_path, monkeypatch):
        bundled = _fake_bundled(tmp_path, {"stage-source.sh": "new\n"})
        work = _work_tree(tmp_path, {"env-linux.sh": "x\n"})
        res = _run(monkeypatch, bundled, [str(work), "--dry-run"])
        assert res.exit_code == 0, res.output
        assert "would add" in res.output
        assert not (work / "_common" / "stage-source.sh").exists(), "dry-run wrote files"

    def test_preserves_unrelated_local_files(self, tmp_path, monkeypatch):
        # A work tree may carry client-specific helpers; syncing must not purge them.
        bundled = _fake_bundled(tmp_path, {"env-linux.sh": "new\n"})
        work = _work_tree(tmp_path, {"env-linux.sh": "old\n", "client-only.sh": "keep me\n"})
        res = _run(monkeypatch, bundled, [str(work)])
        assert res.exit_code == 0, res.output
        assert (work / "_common" / "client-only.sh").read_text() == "keep me\n"

    def test_nested_helper_files_are_synced(self, tmp_path, monkeypatch):
        bundled = _fake_bundled(tmp_path, {"sub/helper.ps1": "nested\n"})
        work = _work_tree(tmp_path, {"env-linux.sh": "x\n"})
        res = _run(monkeypatch, bundled, [str(work)])
        assert res.exit_code == 0, res.output
        assert (work / "_common" / "sub" / "helper.ps1").read_text() == "nested\n"

    def test_same_tree_is_refused_gracefully(self, tmp_path, monkeypatch):
        bundled = _fake_bundled(tmp_path, {"env-linux.sh": "x\n"})
        res = _run(monkeypatch, bundled, [str(bundled)])
        assert res.exit_code == 0, res.output
        assert "nothing to do" in res.output

    def test_missing_recipes_dir_errors(self, tmp_path, monkeypatch):
        bundled = _fake_bundled(tmp_path, {"env-linux.sh": "x\n"})
        res = _run(monkeypatch, bundled, [str(tmp_path / "nope")])
        assert res.exit_code != 0
