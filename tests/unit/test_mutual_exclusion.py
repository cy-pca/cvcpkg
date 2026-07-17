"""Tests for mutually exclusive packages — `provides:` slots and file overlaps.

Two mechanisms, doing different jobs:

* `conflicts:` / `provides:` are *declared*, and are what the installer
  enforces — a conflict must be caught before anything is downloaded, when all
  cvcpkg has is the catalog.
* file overlap is *computed* from bundle manifests, and verifies the
  declarations after the fact.  It cannot gate an install (too late, and only
  sees built packages), but it catches the clobber nobody declared.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from cvcpkg.builder import Recipe, collect_recipe_conflicts
from cvcpkg.file_conflicts import (
    asymmetric_conflicts,
    find_file_overlaps,
    undeclared_conflicts,
)

REPO = Path(__file__).resolve().parents[2]


def _write(d: Path, name: str, **extra) -> None:
    (d / name).mkdir(parents=True, exist_ok=True)
    body = {
        "schema_version": 1,
        "recipe": {"name": name, "upstream_version": "1.0", "cvc_revision": 1},
        "source": {"type": "vendored", "path": "x"},
        "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
        "package": {"files": ["lib/"]},
        **extra,
    }
    with open(d / name / "recipe.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f)


class TestProvidesSlots:
    def test_slot_makes_providers_mutually_exclusive(self, tmp_path):
        _write(tmp_path, "impl-a", provides=["the-slot"])
        _write(tmp_path, "impl-b", provides=["the-slot"])

        got = collect_recipe_conflicts(["impl-a"], [tmp_path])
        assert got["impl-a"] == ["impl-b"]

    def test_slot_exclusion_is_symmetric_by_construction(self, tmp_path):
        # The whole point: neither recipe names the other, yet the conflict
        # fires from either direction.  A pairwise `conflicts:` declaration
        # only fires from the side that declared it.
        _write(tmp_path, "impl-a", provides=["the-slot"])
        _write(tmp_path, "impl-b", provides=["the-slot"])

        assert collect_recipe_conflicts(["impl-a"], [tmp_path])["impl-a"] == ["impl-b"]
        assert collect_recipe_conflicts(["impl-b"], [tmp_path])["impl-b"] == ["impl-a"]

    def test_group_of_n_needs_n_declarations(self, tmp_path):
        # Four alternatives; each declares one slot, not three conflicts.
        names = ["impl-a", "impl-b", "impl-c", "impl-d"]
        for n in names:
            _write(tmp_path, n, provides=["the-slot"])

        for n in names:
            others = sorted(set(names) - {n})
            assert collect_recipe_conflicts([n], [tmp_path])[n] == others

    def test_different_slots_do_not_conflict(self, tmp_path):
        _write(tmp_path, "impl-a", provides=["slot-one"])
        _write(tmp_path, "impl-b", provides=["slot-two"])
        assert collect_recipe_conflicts(["impl-a"], [tmp_path]) == {}

    def test_package_does_not_conflict_with_itself(self, tmp_path):
        _write(tmp_path, "impl-a", provides=["the-slot"])
        assert collect_recipe_conflicts(["impl-a"], [tmp_path]) == {}

    def test_multiple_slots(self, tmp_path):
        _write(tmp_path, "impl-a", provides=["slot-one", "slot-two"])
        _write(tmp_path, "impl-b", provides=["slot-one"])
        _write(tmp_path, "impl-c", provides=["slot-two"])
        got = collect_recipe_conflicts(["impl-a"], [tmp_path])
        assert got["impl-a"] == ["impl-b", "impl-c"]

    def test_explicit_conflicts_still_honoured(self, tmp_path):
        _write(tmp_path, "old-a", conflicts=["old-b"])
        _write(tmp_path, "old-b", conflicts=["old-a"])
        assert collect_recipe_conflicts(["old-a"], [tmp_path])["old-a"] == ["old-b"]

    def test_explicit_and_slot_combine_without_duplicates(self, tmp_path):
        _write(tmp_path, "impl-a", provides=["the-slot"], conflicts=["impl-b"])
        _write(tmp_path, "impl-b", provides=["the-slot"])
        # impl-b is reachable both ways; it must be reported once.
        assert collect_recipe_conflicts(["impl-a"], [tmp_path])["impl-a"] == ["impl-b"]

    def test_no_slots_means_no_full_scan_needed(self, tmp_path):
        # Recipes without `provides` behave exactly as before.
        _write(tmp_path, "plain-a")
        _write(tmp_path, "plain-b")
        assert collect_recipe_conflicts(["plain-a"], [tmp_path]) == {}

    def test_provides_parsed_onto_recipe(self, tmp_path):
        _write(tmp_path, "impl-a", provides=["the-slot"])
        assert Recipe.load(tmp_path / "impl-a").provides == ["the-slot"]

    def test_absent_provides_defaults_empty(self, tmp_path):
        _write(tmp_path, "plain-a")
        assert Recipe.load(tmp_path / "plain-a").provides == []


class TestFileOverlaps:
    def test_detects_shared_path(self):
        ov = find_file_overlaps(
            {
                "a": ["bin/tool", "lib/liba.so"],
                "b": ["bin/tool", "lib/libb.so"],
            }
        )
        assert len(ov) == 1
        assert ov[0].pair == ("a", "b")
        assert ov[0].paths == ("bin/tool",)

    def test_no_overlap(self):
        assert find_file_overlaps({"a": ["lib/liba.so"], "b": ["lib/libb.so"]}) == []

    def test_normalizes_separators_and_leading_slash(self):
        # A Windows-built manifest must still compare against a POSIX one.
        ov = find_file_overlaps({"a": [r"bin\tool"], "b": ["/bin/tool"]})
        assert len(ov) == 1

    def test_three_way_overlap_reports_each_pair(self):
        ov = find_file_overlaps({"a": ["bin/x"], "b": ["bin/x"], "c": ["bin/x"]})
        assert {o.pair for o in ov} == {("a", "b"), ("a", "c"), ("b", "c")}

    def test_ignore_list(self):
        ov = find_file_overlaps(
            {"a": ["share/registry.txt"], "b": ["share/registry.txt"]},
            ignore=frozenset({"share/registry.txt"}),
        )
        assert ov == []

    def test_undeclared_conflict_is_reported(self):
        # The case worth catching: two packages clobber, nobody declared it.
        got = undeclared_conflicts({"a": ["bin/x"], "b": ["bin/x"]}, declared={})
        assert len(got) == 1
        assert got[0].pair == ("a", "b")

    def test_declared_conflict_is_not_reported(self):
        got = undeclared_conflicts(
            {"a": ["bin/x"], "b": ["bin/x"]}, declared={"a": ["b"], "b": ["a"]}
        )
        assert got == []

    def test_one_sided_declaration_counts_as_declared(self):
        # Asymmetry is a separate finding; don't double-report it here.
        got = undeclared_conflicts({"a": ["bin/x"], "b": ["bin/x"]}, declared={"a": ["b"]})
        assert got == []


class TestAsymmetry:
    def test_one_sided_declaration_detected(self):
        # A declares B, B is silent -> installing B onto an existing A is not
        # caught, because the check loads the recipes being installed.
        assert asymmetric_conflicts({"a": ["b"], "b": []}) == [("a", "b")]

    def test_symmetric_is_clean(self):
        assert asymmetric_conflicts({"a": ["b"], "b": ["a"]}) == []

    def test_unloaded_counterpart_is_not_judged(self):
        # 'b' absent from the mapping means it was never loaded, not that it
        # failed to declare.
        assert asymmetric_conflicts({"a": ["b"]}) == []


class TestShippedRecipes:
    """The real catalog must not carry the drift this module exists to catch."""

    def test_shipped_conflicts_are_symmetric(self):
        recipes_dir = REPO / "recipes"
        declared: dict[str, list[str]] = {}
        for d in sorted(recipes_dir.iterdir()):
            if not (d / "recipe.yaml").is_file():
                continue
            try:
                r = Recipe.load(d)
            except Exception:
                continue
            declared[r.name] = list(r.conflicts)

        bad = asymmetric_conflicts(declared)
        assert not bad, (
            "one-sided conflict declarations (the reverse install is not caught):\n  "
            + "\n  ".join(f"{a} declares {b}, but {b} does not declare {a}" for a, b in bad)
        )

    def test_python313_variants_are_mutually_exclusive(self):
        # They install the same lib/python3.13 stdlib tree, so they clobber.
        got = collect_recipe_conflicts(["python313"], [REPO / "recipes"])
        assert "python313t" in got.get("python313", [])
        got = collect_recipe_conflicts(["python313t"], [REPO / "recipes"])
        assert "python313" in got.get("python313t", [])
