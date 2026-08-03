"""Tests for tools/gen_python_recipes.py — the per-interpreter column matrix.

Every Python package is emitted as ``<name>-cp311 / -cp312 / -cp313 /
-cp313t`` column recipes.  The subtle cases:

* a stable-ABI package (cryptography, bcrypt) that ALSO ships free-threaded
  (``cp313-cp313t``) or newer-Python (``cp314``) per-version wheels — the
  abi3 wheel must serve the non-free-threaded columns, the exact cp313t
  wheel (when present) must serve the cp313t column, and the cp314 wheels
  must be ignored;
* the free-threaded column has NO stable ABI, so an abi3-only package must
  not get a cp313t column;
* a column is only viable when every python dep has that column too
  (transitive pruning).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GEN = Path(__file__).resolve().parents[2] / "tools" / "gen_python_recipes.py"
_spec = importlib.util.spec_from_file_location("gen_python_recipes", _GEN)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

INTERPS = ["311", "312", "313", "313t"]


def _w(*filenames):
    """Fake PyPI wheel dicts — only the filename drives classification."""
    return [
        {
            "filename": fn,
            "url": f"https://files.pythonhosted.org/x/{fn}",
            "digests": {"sha256": "0" * 64},
        }
        for fn in filenames
    ]


# Real-world wheel sets (linux x86_64 slice, trimmed).
CRYPTOGRAPHY = _w(
    "cryptography-48.0.0-cp311-abi3-manylinux_2_28_x86_64.whl",
    "cryptography-48.0.0-cp311-abi3-musllinux_1_2_x86_64.whl",
    "cryptography-48.0.0-cp314-cp314t-manylinux_2_28_x86_64.whl",  # free-threaded 3.14
)
BCRYPT = _w(
    "bcrypt-5.0.0-cp38-abi3-manylinux_2_28_x86_64.whl",
    "bcrypt-5.0.0-cp313-cp313t-manylinux_2_28_x86_64.whl",  # free-threaded
    "bcrypt-5.0.0-cp314-cp314t-manylinux_2_28_x86_64.whl",
)
ASYNCPG = _w(
    "asyncpg-0.31.0-cp311-cp311-manylinux_2_28_x86_64.whl",
    "asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl",
    "asyncpg-0.31.0-cp313-cp313-manylinux_2_28_x86_64.whl",
)
MARKUPSAFE = _w(
    "markupsafe-3.0.3-cp313-cp313-manylinux_2_28_x86_64.whl",
    "markupsafe-3.0.3-cp313-cp313t-manylinux_2_28_x86_64.whl",  # real free-threaded
)
UNIVERSAL2 = _w("bcrypt-5.0.0-cp38-abi3-macosx_10_12_universal2.whl")
PURE = _w("alembic-1.18.4-py3-none-any.whl")


class TestClassify:
    def test_pure_none_any(self):
        assert gen.classify(PURE, INTERPS) == "pure"

    @pytest.mark.parametrize("wheels", [CRYPTOGRAPHY, BCRYPT])
    def test_hybrid_stable_abi_is_abi3(self, wheels):
        # abi3 present, and no NON-free-threaded per-version wheel for a target
        # interpreter — the cp3NNt / cp314 wheels must be ignored.
        assert gen.classify(wheels, INTERPS) == "abi3"

    def test_true_per_version_is_cext(self):
        assert gen.classify(ASYNCPG, INTERPS) == "cext"

    def test_perversion_for_target_interp_beats_abi3(self):
        # If a package genuinely ships cp312-cp312 for a target interpreter, it
        # is per-version there even if an abi3 wheel also exists.
        mixed = _w(
            "x-1.0-cp311-abi3-manylinux_2_28_x86_64.whl",
            "x-1.0-cp312-cp312-manylinux_2_28_x86_64.whl",
        )
        assert gen.classify(mixed, INTERPS) == "cext"


class TestWheelSelection:
    def test_abi3_wheel_prefers_manylinux(self):
        w = gen.abi3_wheel_for(CRYPTOGRAPHY, "linux-x86_64", "312")
        assert w is not None and "manylinux" in w["filename"] and "abi3" in w["filename"]

    def test_abi3_wheel_respects_floor(self):
        # a cp311-abi3 wheel serves 3.11+ but a hypothetical 3.10 column never.
        assert gen.abi3_wheel_for(CRYPTOGRAPHY, "linux-x86_64", "310") is None

    def test_abi3_wheel_never_serves_free_threaded(self):
        # The free-threaded build does not implement the stable ABI.
        assert gen.abi3_wheel_for(CRYPTOGRAPHY, "linux-x86_64", "313t") is None

    def test_cext_wheel_exact_non_free_threaded(self):
        w = gen.cext_wheel_for(ASYNCPG, "312", "linux-x86_64")
        assert w is not None and "-cp312-cp312-" in w["filename"]

    def test_cext_wheel_does_not_match_free_threaded_only(self):
        # bcrypt has cp313-cp313t (free-threaded) but no cp313-cp313 — a
        # non-free-threaded 3.13 column must NOT pick up the free-threaded wheel.
        assert gen.cext_wheel_for(BCRYPT, "313", "linux-x86_64") is None

    def test_cext_wheel_matches_free_threaded_column(self):
        w = gen.cext_wheel_for(MARKUPSAFE, "313t", "linux-x86_64")
        assert w is not None and "-cp313-cp313t-" in w["filename"]

    def test_cext_wheel_ignores_abi3(self):
        # Stable-ABI wheels are handled by abi3_wheel_for; the per-version
        # selector must not fall back to them.
        assert gen.cext_wheel_for(CRYPTOGRAPHY, "312", "linux-x86_64") is None

    def test_universal2_serves_both_macos_arches(self):
        assert gen.wheel_matches_platform(UNIVERSAL2[0]["filename"], "macos-x86_64")
        assert gen.wheel_matches_platform(UNIVERSAL2[0]["filename"], "macos-arm64")


class TestWheelForColumn:
    def test_pure_same_wheel_every_column(self):
        m = {"kind": "pure", "wheels": PURE}
        for i in INTERPS:
            assert gen.wheel_for_column(m, i) == {"any": PURE[0]}

    def test_abi3_package_gets_exact_free_threaded_wheel_when_shipped(self):
        # bcrypt: abi3 for 311/312/313, exact cp313t for the free-threaded column.
        m = {"kind": "abi3", "wheels": BCRYPT}
        assert "-abi3-" in gen.wheel_for_column(m, "312")["linux-x86_64"]["filename"]
        assert "-cp313t-" in gen.wheel_for_column(m, "313t")["linux-x86_64"]["filename"]

    def test_abi3_only_package_has_no_free_threaded_column(self):
        m = {"kind": "abi3", "wheels": CRYPTOGRAPHY}
        assert gen.wheel_for_column(m, "313t") == {}

    def test_column_abi_reports_abi3_for_stable_wheels(self):
        m = {"kind": "abi3", "wheels": CRYPTOGRAPHY}
        arts = gen.wheel_for_column(m, "312")
        assert gen.column_abi(m, "312", arts) == "abi3"
        m = {"kind": "cext", "wheels": MARKUPSAFE}
        arts = gen.wheel_for_column(m, "313t")
        assert gen.column_abi(m, "313t", arts) == "cp313t"


class TestComputeColumns:
    def _meta(self):
        return {
            "cryptography": {"kind": "abi3", "wheels": CRYPTOGRAPHY, "deps": {}},
            # pure package depending on cryptography: its cp313t column must be
            # pruned because cryptography has none.
            "pyjwt": {"kind": "pure", "wheels": PURE, "deps": {"cryptography": {}}},
            "click": {"kind": "pure", "wheels": PURE, "deps": {}},
        }

    def test_viability_prunes_transitively(self, capsys):
        cols = gen.compute_columns(self._meta(), INTERPS)
        assert cols["click"] == INTERPS
        assert cols["cryptography"] == ["311", "312", "313"]
        assert cols["pyjwt"] == ["311", "312", "313"]

    def test_marker_excluded_dep_does_not_prune(self):
        meta = self._meta()
        # dep excluded on every column >= 3.11: must not prune anything.
        meta["pyjwt"]["deps"] = {"cryptography": {"markers": 'python_version < "3.10"'}}
        cols = gen.compute_columns(meta, INTERPS)
        assert cols["pyjwt"] == INTERPS


class TestMarkers:
    def test_python_version_lt(self):
        spec = {"markers": 'python_version < "3.11"'}
        assert not gen.marker_ok(spec, "311")
        assert not gen.marker_ok(spec, "313t")

    def test_python_version_ge(self):
        spec = {"markers": 'python_version >= "3.13"'}
        assert not gen.marker_ok(spec, "312")
        assert gen.marker_ok(spec, "313")
        assert gen.marker_ok(spec, "313t")  # 3.13t evaluates as 3.13

    def test_non_version_markers_are_kept(self):
        assert gen.marker_ok({"markers": 'platform_machine == "x86_64"'}, "311")
        assert gen.marker_ok("1.2.3", "311")  # plain string spec

    def test_or_markers_are_kept_conservatively(self):
        spec = {"markers": 'python_version < "3.11" or sys_platform == "win32"'}
        assert gen.marker_ok(spec, "313")

    def test_extras_gated_deps_are_not_hard_edges(self):
        # pyjwt -> cryptography {optional, extra == "crypto"}: the consumer
        # that requires the extra carries its own direct dep; pyjwt's cp313t
        # column must not be pruned by an extra it never needs.
        assert not gen.marker_ok({"optional": True, "markers": ""}, "313")
        assert not gen.marker_ok({"markers": 'extra == "crypto"'}, "313")
        assert not gen.marker_ok(
            {"markers": 'python_version < "3.11" or extra == "asyncio"'}, "313"
        )

    def test_pep440_zero_padding(self):
        # 3.11 == 3.11.0: a '< "3.11.0"' marker excludes the cp311 column.
        assert not gen.marker_ok({"markers": 'python_version < "3.11.0"'}, "311")
        assert gen.marker_ok({"markers": 'python_version <= "3.11.0"'}, "311")


class TestColVersion:
    def test_plain(self):
        assert gen.col_version("311") == "3.11"

    def test_free_threaded(self):
        assert gen.col_version("313t") == "3.13t"
        assert gen.col_digits("313t") == "313"


class TestWriteRecipe:
    """_write_recipe protects published revisions across regenerations."""

    def _write(self, tmp_path, body, floor=1):
        gen._write_recipe(tmp_path / "r", body, floor=floor)
        return (tmp_path / "r" / "recipe.yaml").read_text()

    def test_fresh_dir_starts_at_floor(self, tmp_path):
        assert "cvc_revision: 2" in self._write(tmp_path, "cvc_revision: {rev}\nx\n", floor=2)

    def test_unchanged_body_keeps_revision(self, tmp_path):
        self._write(tmp_path, "cvc_revision: {rev}\nx\n", floor=2)
        assert "cvc_revision: 2" in self._write(tmp_path, "cvc_revision: {rev}\nx\n", floor=2)

    def test_changed_body_bumps_above_floor(self, tmp_path):
        self._write(tmp_path, "cvc_revision: {rev}\nx\n", floor=2)
        out = self._write(tmp_path, "cvc_revision: {rev}\ny\n", floor=2)
        assert "cvc_revision: 3" in out

    def test_changed_body_respects_higher_floor(self, tmp_path):
        self._write(tmp_path, "cvc_revision: {rev}\nx\n", floor=1)
        out = self._write(tmp_path, "cvc_revision: {rev}\ny\n", floor=5)
        assert "cvc_revision: 5" in out


class TestPruneStale:
    def test_never_deletes_a_non_wheel_recipe(self, tmp_path):
        # The restored C++ protobuf shares its base name with the python
        # columns; the guard must keep it.
        d = tmp_path / "protobuf"
        d.mkdir()
        (d / "recipe.yaml").write_text("source:\n  type: tarball\n")
        meta = {"protobuf": {"kind": "pure", "wheels": PURE, "deps": {}}}
        gen._prune_stale(tmp_path, meta, {"protobuf": ["311"]}, ["311"])
        assert d.is_dir()

    def test_deletes_superseded_bare_wheel_dir(self, tmp_path):
        d = tmp_path / "click"
        d.mkdir()
        (d / "recipe.yaml").write_text("source:\n  type: python_wheel\n")
        meta = {"click": {"kind": "pure", "wheels": PURE, "deps": {}}}
        gen._prune_stale(tmp_path, meta, {"click": ["311"]}, ["311"])
        assert not d.exists()

    def test_orphan_sweep_removes_generator_owned_dirs_only(self, tmp_path):
        gone = tmp_path / "leftpad-cp311"
        gone.mkdir()
        (gone / "recipe.yaml").write_text("description: generated by tools/gen_python_recipes.py\n")
        hand = tmp_path / "numpy-cp311"
        hand.mkdir()
        (hand / "recipe.yaml").write_text("description: hand-written from-source column\n")
        gen._prune_stale(tmp_path, {}, {}, ["311"])
        assert not gone.exists() and hand.is_dir()
