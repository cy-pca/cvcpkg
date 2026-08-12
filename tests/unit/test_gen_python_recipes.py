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

Source mode (hermeticity) is the second axis:

* the DEFAULT is a from-source build off the PyPI **sdist**
  (``source.type: tarball``), because a prebuilt wheel — including a
  ``py3-none-any`` one — is a third-party binary artifact;
* ``--no-build-isolation`` makes the PEP-517 backend a real ``depends.build``
  edge, read out of the sdist's ``pyproject.toml``; a backend with no cvcpkg
  recipe blocks the conversion and the package must stay prebuilt rather than
  emit a dangling edge;
* the CUDA binary redistributables (``nvidia-*-cu12``, ``torch``, ``triton``)
  have no buildable source and stay prebuilt unconditionally.
"""

from __future__ import annotations

import importlib.util
import io
import re
import shutil
import sys
import tarfile
import zipfile
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


def _sd(filename="widget-1.0.tar.gz", sha="a" * 64):
    """Fake PyPI sdist dict (the shape ``urls[]`` entries have)."""
    return {
        "filename": filename,
        "packagetype": "sdist",
        "url": f"https://files.pythonhosted.org/s/{filename}",
        "digests": {"sha256": sha},
    }


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


class TestHandWrittenDepColumns:
    """Hand-written column families (numpy) as resolvable dependency edges.

    Before HANDWRITTEN_DEP_BASES, a seed dep on ``numpy`` was silently dropped
    by deps_for_column's universe test: trimesh-cp311 shipped with NO numpy
    edge and imported only when a sibling build left numpy in the shared
    prefix.  These tests pin the fix: the edge renders, the columns come from
    disk, and a dependent claiming a column the family lacks is pruned."""

    def _seed_meta(self):
        # A pure seed depending on numpy — the gymnasium/trimesh shape.
        return {
            "gymnasium": {
                "kind": "pure",
                "wheels": PURE,
                "deps": ["numpy"],
                "seed": True,
            },
        }

    def test_columns_come_from_disk(self, tmp_path):
        for i in ("311", "313t"):
            d = tmp_path / f"numpy-cp{i}"
            d.mkdir()
            (d / "recipe.yaml").write_text("recipe:\n  name: numpy\n")
        (tmp_path / "numpy-cp312").mkdir()  # dir without recipe.yaml: not a column
        assert gen.handwritten_columns(tmp_path, INTERPS) == {"numpy": ["311", "313t"]}

    def test_dep_edge_resolves_against_extra_universe(self):
        meta = self._seed_meta()
        cols = gen.compute_columns(meta, INTERPS, {"numpy": ["311", "312", "313", "313t"]})
        assert cols["gymnasium"] == INTERPS
        assert cols["numpy"] == INTERPS  # seeded through untouched

    def test_dependent_prunes_to_the_family_s_columns(self, capsys):
        meta = self._seed_meta()
        cols = gen.compute_columns(meta, INTERPS, {"numpy": ["311", "312", "313"]})
        # numpy has no cp313t recipe on disk -> gymnasium must not claim one.
        assert cols["gymnasium"] == ["311", "312", "313"]
        assert "prune gymnasium-cp313t" in capsys.readouterr().err

    def test_without_the_table_the_edge_still_drops(self):
        # The historical behaviour, pinned so the fix's mechanism is explicit:
        # no extra universe -> the dep neither resolves nor prunes.
        cols = gen.compute_columns(self._seed_meta(), INTERPS)
        assert cols["gymnasium"] == INTERPS

    def test_seed_interpreter_cap_prunes_the_package_and_its_dependents(self, capsys):
        # pillow's shape: upstream ships cp313t wheels, but the hand-written
        # conversion covers 311/312/313 only — the cap must hold the column
        # back AND prune dependents through the fixpoint, or a full regen
        # emits a generated pillow-cp313t with no native edges.
        meta = {
            "pillow": {
                "kind": "pure",
                "wheels": PURE,
                "deps": [],
                "seed": True,
                "interpreters": ["311", "312", "313"],
            },
            "imageio": {"kind": "pure", "wheels": PURE, "deps": ["pillow"], "seed": True},
        }
        cols = gen.compute_columns(meta, INTERPS)
        assert cols["pillow"] == ["311", "312", "313"]
        assert cols["imageio"] == ["311", "312", "313"]
        err = capsys.readouterr().err
        assert "prune pillow-cp313t: column capped" in err
        assert "prune imageio-cp313t: dep(s) lack the column: pillow" in err

    def test_emitted_recipe_carries_the_edge(self, tmp_path):
        m = _meta_sdist(deps=["numpy"], seed=True, build_requires=["setuptools"])
        meta = {"widget": m}
        gen._emit_column(
            tmp_path, "widget", m, "311", meta, {"widget": ["311"]}, extra_universe={"numpy"}
        )
        y = (tmp_path / "widget-cp311" / "recipe.yaml").read_text(encoding="utf-8")
        build, _, runtime = y.partition("  runtime:\n")
        assert "- name: numpy-cp311" in build
        assert "- name: numpy-cp311" in runtime

    def test_shipped_table_resolves_against_the_real_recipes(self):
        # The table's bases must have real columns on disk, or every run
        # aborts; this is the same check main() enforces, pinned in CI.
        recipes = Path(__file__).resolve().parents[2] / "recipes"
        cols = gen.handwritten_columns(recipes, INTERPS)
        for base in gen.HANDWRITTEN_DEP_BASES:
            assert cols[base], f"{base} has no {base}-cpNNN recipes on disk"


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

    def test_deletes_superseded_generated_sdist_dir(self, tmp_path):
        # A from-source generated column is `type: tarball`, so the historical
        # "is it python_wheel?" ownership test no longer identifies it — the
        # generator marker does.  Without this it would linger forever.
        d = tmp_path / "click"
        d.mkdir()
        (d / "recipe.yaml").write_text(
            "source:\n  type: tarball\n" "  description: generated by tools/gen_python_recipes.py\n"
        )
        meta = {"click": {"kind": "pure", "wheels": PURE, "deps": {}}}
        gen._prune_stale(tmp_path, meta, {"click": ["311"]}, ["311"])
        assert not d.exists()

    def test_keeps_hand_written_from_source_column(self, tmp_path):
        # numpy-cp311/h5py-cp311 are hand-written `type: tarball` from-source
        # recipes.  They must survive even when their base is managed.
        d = tmp_path / "numpy-cp312"
        d.mkdir()
        (d / "recipe.yaml").write_text("source:\n  type: tarball\n# hand-written\n")
        meta = {"numpy": {"kind": "cext", "wheels": ASYNCPG, "deps": {}}}
        gen._prune_stale(tmp_path, meta, {"numpy": ["311"]}, ["311", "312"])
        assert d.is_dir()

    def test_prose_denial_of_python_wheel_is_not_ownership(self, tmp_path):
        # The sentence every hand-converted from-source recipe carries.  A
        # substring test for "python_wheel" reads this DENIAL as a declaration
        # and deletes the recipe; only a line-anchored source.type match is
        # ownership.  cffi/markupsafe/pyyaml/greenlet's bases are all managed,
        # so this test is the only thing between them and shutil.rmtree.
        d = tmp_path / "cffi-cp313"
        d.mkdir()
        (d / "recipe.yaml").write_text(
            "# From-source sdist (NOT source.type python_wheel): cvcpkg fetches and\n"
            "# sha256-verifies this tarball and extracts it to CVC_SOURCE_DIR.\n"
            "source:\n  type: tarball\n"
        )
        meta = {"cffi": {"kind": "cext", "wheels": ASYNCPG, "deps": {}}}
        gen._prune_stale(tmp_path, meta, {"cffi": ["311", "312"]}, ["311", "312", "313"])
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


# ── From-source (sdist) support ─────────────────────────────────────────────


class TestGeneratorOwnership:
    """A regeneration must not silently undo a hand conversion.

    numpy/h5py/cffi-style from-source recipes declare native-library edges
    (libffi, hdf5, openblas), rpath passes and per-platform gates that
    ``[build-system] requires`` cannot reveal.  They are identified by the
    ABSENCE of the generator marker, which every emitted recipe carries.
    """

    def test_new_column_is_writable(self, tmp_path):
        assert gen.is_generator_owned(tmp_path / "does-not-exist-cp311")

    def test_marked_recipe_is_writable(self, tmp_path):
        d = tmp_path / "click-cp311"
        d.mkdir()
        (d / "recipe.yaml").write_text(f"description: {gen.GENERATOR_MARKER}\n")
        assert gen.is_generator_owned(d)

    def test_hand_written_recipe_is_protected(self, tmp_path):
        # cffi-cp311 was hand-converted and carries a `libffi` edge; a
        # regeneration would drop it and link the SYSTEM libffi instead.
        d = tmp_path / "cffi-cp311"
        d.mkdir()
        (d / "recipe.yaml").write_text(
            "source:\n  type: tarball\ndepends:\n  build:\n    - name: libffi\n"
        )
        assert not gen.is_generator_owned(d)

    @pytest.mark.parametrize("mode", ["sdist", "wheel"])
    def test_emitted_recipes_are_recognised_as_owned(self, tmp_path, mode):
        # Round-trip: if the emitted marker ever drifts from the ownership
        # test, a regeneration would refuse to update its own recipes and
        # _prune_stale would stop collecting orphans.  Both shapes must match.
        m = _meta_sdist(mode=mode)
        gen._emit_column(tmp_path, "widget", m, "311", {"widget": m, "colorama": {}}, {})
        assert gen.is_generator_owned(tmp_path / "widget-cp311")

    def test_shipped_hand_written_from_source_columns_are_protected(self):
        # The real recipes this guard exists for: hand-converted from-source
        # columns carrying native-library edges the generator cannot infer.
        recipes = Path(__file__).resolve().parents[2] / "recipes"
        for name in ("numpy-cp311", "h5py-cp311"):
            d = recipes / name
            if d.is_dir():
                assert not gen.is_generator_owned(d), name


# ── Hand-written families whose base IS in the managed set ──────────────────
#
# numpy-cp311 and h5py-cp311 survive a regeneration partly by accident: their
# bases are absent from poetry.lock, so the emit loop and _prune_stale never
# reach them at all.  These four have no such luck -- cffi, markupsafe, pyyaml
# and greenlet are all in poetry.lock's `main` group, so a full run walks
# straight through them and the ownership tests are the ONLY thing holding.
#
# What a regeneration would silently destroy, none of which the generator can
# infer from a pyproject's [build-system] requires:
#   cffi       libffi edge (build+runtime, `platforms:`-restricted to non-Windows
#              because MSVC compiles the libffi subset vendored in cffi's own
#              sdist), an $ORIGIN/@loader_path rpath pass, and a pkg-config
#              hermeticity probe -- without which setup.py falls through to a
#              hardcoded /usr/include/ffi and links the SYSTEM libffi, no error.
#   pyyaml     yaml (libyaml) edge, the same rpath pass, and
#              PYYAML_FORCE_LIBYAML=1: without it a build with no reachable
#              yaml.h still "succeeds" and emits a platform-tagged wheel with no
#              yaml/_yaml extension in it -- no CLoader, no error, no warning.
#   markupsafe CIBUILDWHEEL=1: its setup.py catches a failed C compile and
#              re-runs setup() with ext_modules=[], shipping a slow pure-Python
#              wheel that looks perfectly fine.
#   greenlet   hand-written platform/compiler wiring.
# Plus the freebsd/openbsd/netbsd matrix columns, which a default run would
# narrow back to SDIST_PLATFORMS' linux/macos/windows.
_HAND_WRITTEN_COLUMNS = (
    "cffi-cp311",
    "cffi-cp312",
    "cffi-cp313",
    "markupsafe-cp311",
    "markupsafe-cp312",
    "markupsafe-cp313",
    "markupsafe-cp313t",
    "pyyaml-cp311",
    "pyyaml-cp312",
    "pyyaml-cp313",
    "greenlet-cp311",
    "greenlet-cp312",
    "greenlet-cp313",
    # pillow: native-library edges (zlib/libjpeg-turbo/tiff/freetype/libwebp
    # + closures), `-C <feature>=enable` codec pinning and an $ORIGIN rpath
    # pass — its base is a SEED (matplotlib/imageio resolve their pillow dep
    # edges against it), so like the four above, the ownership test is the
    # only thing between it and a regeneration that would strip its codecs.
    "pillow-cp311",
    "pillow-cp312",
    "pillow-cp313",
    # contourpy: meson finds the staged pybind11 only through the
    # --pkgconfigdir step in its hand-written build.sh (the shipped
    # pybind11-config script has a dead ephemeral-prefix shebang); a
    # regeneration would drop that step and every server build dies in
    # meson's dependency probe again.
    "contourpy-cp311",
    "contourpy-cp312",
    "contourpy-cp313",
    # matplotlib: contourpy's pybind11 fix PLUS -Dsystem-freetype/-Dsystem-
    # qhull (the default meson wraps DOWNLOAD both sources — impossible on an
    # offline builder), native freetype/qhull edges, an $ORIGIN rpath pass and
    # a real Agg-render check.  A regeneration would put the downloads back.
    "matplotlib-cp311",
    "matplotlib-cp312",
    "matplotlib-cp313",
)

_REPO = Path(__file__).resolve().parents[2]


def _shipped_columns() -> dict[str, list[str]]:
    """base -> the interpreter columns actually shipped, derived from the
    directory names so the map cannot drift from the tuple above."""
    cols: dict[str, list[str]] = {}
    for name in _HAND_WRITTEN_COLUMNS:
        base, _, interp = name.rpartition("-cp")
        cols.setdefault(base, []).append(interp)
    return cols


class TestHandWrittenColumnsSurviveAFullRun:
    """A FULL generator run must leave these recipes byte-identical.

    Not a ``--only`` run and not a unit call on one helper: ``main()`` end to
    end, because that is the shape with both hazards in it -- the emit loop
    (overwrite) and _prune_stale (deletion).  Preservation from one is not
    preservation from the other, and these families need both.

    PyPI is stubbed so the test is hermetic and fast; every other code path is
    the real one, and the files compared are the real shipped recipes.
    """

    def _run(self, tmp_path, monkeypatch, columns, extra_argv=()):
        """Seed an out/ with the real recipes, stub PyPI, run main().

        *columns* is base -> the interpreter columns PyPI is pretended to
        publish wheels for.  It drives compute_columns, hence which directories
        _prune_stale then considers superseded.
        """
        src = _REPO / "recipes"
        out = tmp_path / "recipes"
        out.mkdir()
        for name in _HAND_WRITTEN_COLUMNS:
            shutil.copytree(src / name, out / name)
        # The PEP-517 backend must resolve, or every column falls back to a
        # prebuilt wheel and the emit path under test is never reached.
        for i in INTERPS:
            (out / f"setuptools-cp{i}").mkdir()

        versions = {b: _upstream_version(src, b) for b in columns}
        lock = tmp_path / "poetry.lock"
        lock.write_text(
            "".join(
                f'[[package]]\nname = "{b}"\nversion = "{v}"\ngroups = ["main"]\n\n'
                for b, v in sorted(versions.items())
            )
        )

        def _fetch(name, version):
            base = gen.norm(name)
            return (
                _w(
                    *[
                        f"{base}-{version}-cp{gen.col_digits(i)}-cp{i}-{tag}.whl"
                        for i in columns[base]
                        for tag in ("manylinux_2_28_x86_64", "macosx_11_0_arm64", "win_amd64")
                    ]
                ),
                _sd(f"{base}-{version}.tar.gz"),
                "MIT",
            )

        monkeypatch.setattr(gen, "fetch_pypi", _fetch)
        monkeypatch.setattr(gen, "sdist_build_requires", lambda sd, cache: ["setuptools"])
        monkeypatch.setattr(gen, "SEED_PACKAGES", {})
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "gen_python_recipes.py",
                "--lock",
                str(lock),
                "--out",
                str(out),
                "--sdist-cache",
                str(tmp_path / "cache"),
                *extra_argv,
            ],
        )
        assert gen.main() == 0
        return out

    def _diffs(self, out) -> list[str]:
        """Names of shipped files a run deleted or rewrote."""
        src = _REPO / "recipes"
        bad = []
        for name in _HAND_WRITTEN_COLUMNS:
            if not (out / name).is_dir():
                bad.append(f"{name}: DELETED")
                continue
            for f in sorted(p.name for p in (src / name).iterdir() if p.is_file()):
                if not (out / name / f).is_file():
                    bad.append(f"{name}/{f}: DELETED")
                elif (out / name / f).read_bytes() != (src / name / f).read_bytes():
                    bad.append(f"{name}/{f}: REWRITTEN")
        return bad

    def test_full_run_leaves_them_byte_identical(self, tmp_path, monkeypatch):
        out = self._run(tmp_path, monkeypatch, _shipped_columns())
        assert self._diffs(out) == []

    def test_a_column_leaving_the_matrix_does_not_delete_it(self, tmp_path, monkeypatch):
        # The realistic trigger: a column stops being viable -- upstream drops
        # a wheel for it, a dependency loses the column and prunes it
        # transitively, or the package goes abi3-only (which has no stable ABI
        # for cp313t).  _prune_stale then reaches the directory -- and every one
        # of these recipes contains the literal string "python_wheel" in the
        # comment DENYING that it is one, which a substring ownership test reads
        # as a licence to rmtree.
        columns = _shipped_columns()
        columns["markupsafe"] = ["311", "312", "313"]  # cp313t leaves
        columns["cffi"] = ["311", "312"]  # cp313 leaves
        out = self._run(tmp_path, monkeypatch, columns)
        assert self._diffs(out) == []

    def test_the_guard_is_what_holds_not_the_harness(self, tmp_path, monkeypatch):
        # Negative control: with --overwrite-hand-written the same run DOES
        # rewrite them.  Without this, a broken guard could still pass the two
        # tests above if the harness never reached the emit loop at all.
        out = self._run(
            tmp_path, monkeypatch, _shipped_columns(), extra_argv=["--overwrite-hand-written"]
        )
        assert self._diffs(out), "emit loop was never reached — the tests above prove nothing"


def _upstream_version(recipes: Path, base: str) -> str:
    """The version a shipped column pins, so the synthetic poetry.lock tracks
    the real recipes instead of going stale on the next version bump."""
    text = (recipes / f"{base}-cp311" / "recipe.yaml").read_text(encoding="utf-8")
    return re.search(r'upstream_version:\s*"([^"]+)"', text).group(1)


class TestPruneOwnership:
    """``source.type: python_wheel`` as a field, never as a substring."""

    def test_real_declaration_is_ownership(self):
        assert gen._declares_python_wheel("source:\n  type: python_wheel\n")

    def test_prose_denial_is_not(self):
        assert not gen._declares_python_wheel(
            "# From-source sdist (NOT source.type python_wheel): cvcpkg fetches\n"
            "source:\n  type: tarball\n"
        )

    def test_trailing_comment_after_the_field_still_counts(self):
        assert gen._declares_python_wheel("source:\n  type: python_wheel  # historical\n")

    def test_a_url_fragment_is_not_a_comment(self):
        # The comment stripper must require a boundary before '#', or it would
        # eat half a URL and could corrupt the field it is about to match.
        assert gen._declares_python_wheel(
            "source:\n  url: https://example.invalid/x.whl#sha256=abc\n  type: python_wheel\n"
        )

    def test_missing_recipe_yaml_is_nobody_s_to_delete(self, tmp_path):
        (tmp_path / "empty-cp311").mkdir()
        assert not gen.is_prunable(tmp_path / "empty-cp311")

    def test_marker_makes_it_deletable(self, tmp_path):
        d = tmp_path / "click-cp311"
        d.mkdir()
        (d / "recipe.yaml").write_text(f"description: {gen.GENERATOR_MARKER}\n")
        assert gen.is_prunable(d)

    def test_every_shipped_hand_written_column_is_undeletable(self):
        # Content-based, so a NEW hand-written family is covered the day it
        # lands -- no allowlist to remember to update.
        recipes = _REPO / "recipes"
        for d in sorted(recipes.iterdir()):
            y = d / "recipe.yaml"
            if not y.is_file():
                continue
            text = y.read_text(encoding="utf-8")
            if gen.GENERATOR_MARKER in text or gen._declares_python_wheel(text):
                continue
            assert not gen.is_prunable(d), f"{d.name} is hand-written but prunable"


class TestPrebuiltOnlyAllowlist:
    """CUDA binary redistributables have no buildable source at all."""

    @pytest.mark.parametrize(
        "base",
        [
            "nvidia-cublas-cu12",
            "nvidia-cudnn-cu12",
            "nvidia-nccl-cu12",
            "nvidia-cusparselt-cu12",
            "torch",
            "triton",
        ],
    )
    def test_exempt_packages_have_a_documented_reason(self, base):
        reason = gen.prebuilt_only_reason(base)
        assert reason and len(reason) > 20  # not an empty marker: a real why

    @pytest.mark.parametrize("base", ["click", "numpy", "torchvision", "nvidia-ml-py"])
    def test_ordinary_packages_are_not_exempt(self, base):
        # The pattern is anchored: 'torchvision' must not inherit torch's
        # exemption, and a non-CUDA nvidia-* package is not a redistributable.
        assert gen.prebuilt_only_reason(base) is None

    def test_exemption_beats_forced_sdist(self):
        # --source-mode sdist is an operator override, but there is nothing to
        # build for these: the allowlist wins.
        mode, reason = gen.source_mode_for(
            "nvidia-cublas-cu12",
            kind="cext",
            has_sdist=True,
            missing=[],
            pure_policy="sdist",
            forced="sdist",
        )
        assert mode == "wheel" and "NVIDIA" in reason


class TestSdistSelection:
    def test_picks_the_sdist_entry(self):
        files = [
            {"filename": "w-1.0-py3-none-any.whl", "packagetype": "bdist_wheel"},
            _sd("w-1.0.tar.gz"),
        ]
        assert gen.sdist_from(files)["filename"] == "w-1.0.tar.gz"

    def test_prefers_tarball_over_zip(self):
        files = [_sd("w-1.0.zip"), _sd("w-1.0.tar.gz")]
        assert gen.sdist_from(files)["filename"] == "w-1.0.tar.gz"

    def test_none_when_wheel_only(self):
        assert gen.sdist_from([{"filename": "w.whl", "packagetype": "bdist_wheel"}]) is None


class TestBuildRequires:
    def test_req_name_normalizes(self):
        assert gen.req_name("setuptools_scm[toml] >= 7, < 10") == "setuptools-scm"
        assert gen.req_name("flit_core >=3.8,<4") == "flit-core"
        assert gen.req_name("hatchling") == "hatchling"

    def test_python_version_marker_is_evaluated(self):
        # Every column we ship is >= 3.11, so a backport pin drops out.
        assert not gen.req_applies('tomli >= 1.0.0; python_version < "3.11"', "311")
        assert gen.req_applies('meson >= 1.2.3; python_version >= "3.12"', "313")

    def test_non_version_markers_are_kept(self):
        # We always build on CPython; keeping an extra edge is safe, dropping a
        # needed one is not.
        assert gen.req_applies("cffi>=2.0.0; platform_python_implementation != 'PyPy'", "311")

    def test_parse_build_requires(self):
        toml = '[build-system]\nrequires = ["hatchling", "hatch-vcs>=0.4"]\n'
        assert gen.parse_build_requires(toml) == ["hatchling", "hatch-vcs>=0.4"]

    def test_parse_build_requires_none_without_build_system(self):
        # A pyproject.toml that only carries [tool.*] declares no backend, so
        # PEP 517's setuptools fallback applies — signalled by None.
        assert gen.parse_build_requires("[tool.black]\nline-length = 100\n") is None

    def _tar(self, tmp_path, members: dict[str, str], name="widget-1.0.tar.gz"):
        p = tmp_path / name
        with tarfile.open(p, "w:gz") as t:
            for path, body in members.items():
                data = body.encode()
                info = tarfile.TarInfo(path)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return p

    def test_reads_root_pyproject_from_tarball(self, tmp_path):
        blob = self._tar(
            tmp_path,
            {
                "widget-1.0/pyproject.toml": '[build-system]\nrequires = ["hatchling"]\n',
                # A vendored subpackage's pyproject.toml is NOT the build config.
                "widget-1.0/vendor/dep/pyproject.toml": '[build-system]\nrequires = ["nope"]\n',
            },
        )
        assert gen.parse_build_requires(gen._sdist_pyproject(blob)) == ["hatchling"]

    def test_reads_root_pyproject_from_zip(self, tmp_path):
        p = tmp_path / "widget-1.0.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("widget-1.0/pyproject.toml", '[build-system]\nrequires = ["flit_core"]\n')
        assert gen.parse_build_requires(gen._sdist_pyproject(p)) == ["flit_core"]

    def test_legacy_setup_py_sdist_falls_back_to_setuptools(self, tmp_path, monkeypatch):
        # boto3/botocore/six/sympy ship no pyproject.toml at all; PEP 517's
        # documented fallback is setuptools.build_meta:__legacy__.
        blob = self._tar(tmp_path, {"widget-1.0/setup.py": "from setuptools import setup\n"})
        monkeypatch.setattr(gen, "download_sdist", lambda sdist, cache: blob)
        assert gen.sdist_build_requires(_sd(), tmp_path) == ["setuptools", "wheel"]

    def test_download_sdist_caches(self, tmp_path, monkeypatch):
        calls = []

        def _fake(url, dest):
            calls.append(url)
            Path(dest).write_bytes(b"x")

        monkeypatch.setattr(gen.urllib.request, "urlretrieve", _fake)
        sd = _sd()
        assert gen.download_sdist(sd, tmp_path / "c").read_bytes() == b"x"
        gen.download_sdist(sd, tmp_path / "c")  # second call must hit the cache
        assert len(calls) == 1


class TestBackendEdges:
    def test_python_backends_become_columns(self):
        assert gen.backend_edges(["setuptools>=61", "hatchling"], "313t") == [
            "setuptools-cp313t",
            "hatchling-cp313t",
        ]

    def test_native_tools_stay_bare_recipe_names(self):
        # meson-python's build-system.requires names `meson`; cvcpkg ships that
        # as the NATIVE meson recipe, not a meson-cp311 column.
        assert gen.backend_edges(["meson >= 1.2.3", "packaging >= 23.2"], "311") == [
            "meson",
            "packaging-cp311",
        ]

    def test_pip_is_not_an_edge(self):
        assert gen.backend_edges(["pip", "setuptools"], "311") == ["setuptools-cp311"]

    def test_marker_excluded_requirement_is_dropped(self):
        assert gen.backend_edges(['tomli >= 1; python_version < "3.11"'], "311") == []

    def test_duplicate_requirements_collapse(self):
        # pyyaml pins Cython twice, once per python_version range.
        assert gen.backend_edges(
            [
                "setuptools",
                "Cython; python_version < '3.13'",
                "Cython>=3.0; python_version >= '3.13'",
            ],
            "313",
        ) == ["setuptools-cp313", "cython-cp313"]


class TestBackendAvailability:
    def test_available_from_disk_and_this_run(self, tmp_path):
        (tmp_path / "setuptools-cp311").mkdir()
        avail = gen.available_recipes(tmp_path, {"wheel-cp311"})
        assert {"setuptools-cp311", "wheel-cp311"} <= avail
        assert gen.missing_backends(
            ["setuptools-cp311", "wheel-cp311", "hatchling-cp311"], avail
        ) == ["hatchling-cp311"]

    def test_missing_dir_is_not_fatal(self, tmp_path):
        assert gen.available_recipes(tmp_path / "nope") == set()


class TestSourceModePolicy:
    """The documented switch, rule by rule (see source_mode_for)."""

    def _mode(self, **kw):
        base = dict(
            base="widget",
            kind="pure",
            has_sdist=True,
            missing=[],
            pure_policy="sdist",
            forced="auto",
        )
        base.update(kw)
        name = base.pop("base")
        return gen.source_mode_for(name, **base)

    def test_default_is_from_source(self):
        assert self._mode()[0] == "sdist"

    def test_pure_packages_default_to_sdist_too(self):
        # A py3-none-any wheel is still a third-party binary artifact.
        mode, _ = self._mode(kind="pure")
        assert mode == "sdist"

    def test_pure_policy_wheel_restores_prebuilt_noarch(self):
        mode, reason = self._mode(kind="pure", pure_policy="wheel")
        assert mode == "wheel" and "--pure-policy" in reason

    def test_pure_policy_does_not_affect_compiled_packages(self):
        assert self._mode(kind="cext", pure_policy="wheel")[0] == "sdist"

    def test_no_sdist_falls_back_with_a_reason(self):
        mode, reason = self._mode(has_sdist=False)
        assert mode == "wheel" and "no sdist" in reason

    def test_missing_backend_blocks_the_conversion(self):
        mode, reason = self._mode(missing=["hatchling-cp311", "hatch-vcs-cp311"])
        assert mode == "wheel"
        assert "hatch-vcs-cp311" in reason and "hatchling-cp311" in reason

    def test_forced_sdist_overrides_a_missing_backend(self):
        # Deliberate operator escape hatch: emit the from-source recipe so the
        # dangling backend edge fails `cvcpkg validate` loudly.
        mode, reason = self._mode(missing=["hatchling-cp311"], forced="sdist")
        assert mode == "sdist" and "--source-mode" in reason

    def test_forced_wheel_skips_the_sdist_entirely(self):
        assert self._mode(forced="wheel")[0] == "wheel"


class TestSdistPlatforms:
    def test_noarch_package_builds_once_for_posix_plus_a_windows_column(self):
        # The noarch payload is still built ONCE for everything POSIX -- that
        # property is what `any` buys and it is preserved. Windows needs its own
        # entry regardless: a matrix entry names ONE script, `any` names
        # build.sh, and build.sh cannot run on a Windows builder. Without this
        # a pure package was advertised as buildable everywhere and was in fact
        # unbuildable from source on Windows, which is what left setuptools --
        # and therefore every PEP-517 backend, pillow and numpy -- unbuildable
        # there.
        assert gen.sdist_platforms("pure", ["linux", "macos"]) == [
            ("any", "build.sh"),
            ("windows", "build.ps1"),
        ]

    def test_compiled_package_builds_per_platform(self):
        assert gen.sdist_platforms("cext", ["linux", "macos", "windows"]) == [
            ("linux", "build.sh"),
            ("macos", "build.sh"),
            ("windows", "build.ps1"),
        ]

    def test_bsds_are_opt_in_not_default(self):
        # Nothing has ever been built on the BSDs from this generator; claiming
        # them by default would publish an unproven promise.
        assert "freebsd" not in gen.SDIST_PLATFORMS
        assert gen.sdist_platforms("cext", ["linux", "freebsd"]) == [
            ("linux", "build.sh"),
            ("freebsd", "build.sh"),
        ]


class TestColumnAbiSourceMode:
    def test_sdist_column_is_always_the_exact_tag(self):
        # We compile against THIS column's interpreter, so upstream shipping an
        # abi3 wheel says nothing about what our build produces.
        m = {"kind": "abi3", "wheels": CRYPTOGRAPHY}
        arts = gen.wheel_for_column(m, "312")
        assert gen.column_abi(m, "312", arts, "sdist") == "cp312"
        assert gen.column_abi(m, "312", arts, "wheel") == "abi3"


def _meta_sdist(**kw):
    m = {
        "pypi_name": "widget",
        "version": "1.2.3",
        "deps": {"colorama": "*"},
        "wheels": _w("widget-1.2.3-cp311-cp311-manylinux_2_28_x86_64.whl"),
        "sdist": _sd("widget-1.2.3.tar.gz", "b" * 64),
        "kind": "cext",
        "license": "MIT",
        "mode": "sdist",
        "reason": "built from the PyPI sdist",
        "build_requires": ["setuptools>=61", "Cython", 'tomli; python_version < "3.11"'],
        "platforms": ["linux", "macos", "windows"],
    }
    m.update(kw)
    return m


class TestEmitSdistColumn:
    def _emit(self, tmp_path, **kw):
        m = _meta_sdist(**kw)
        meta = {"widget": m, "colorama": {}}
        gen._emit_column(tmp_path, "widget", m, "311", meta, {"widget": ["311"]})
        d = tmp_path / "widget-cp311"
        return (
            (d / "recipe.yaml").read_text(encoding="utf-8"),
            (d / "build.sh").read_text(encoding="utf-8"),
            d,
        )

    def test_source_is_the_verified_sdist_tarball(self, tmp_path):
        y, _, _ = self._emit(tmp_path)
        assert "type: tarball" in y
        assert "python_wheel" not in y
        assert "url: https://files.pythonhosted.org/s/widget-1.2.3.tar.gz" in y
        assert f'sha256: "{"b" * 64}"' in y
        assert "strip_components: 1" in y

    def test_backend_is_a_build_edge_not_a_runtime_one(self, tmp_path):
        y, _, _ = self._emit(tmp_path)
        build, _, runtime = y.partition("  runtime:\n")
        assert "- name: setuptools-cp311" in build and "- name: cython-cp311" in build
        assert "setuptools-cp311" not in runtime and "cython-cp311" not in runtime
        # the runtime dep is also a build dep (the import check needs it)
        assert "- name: colorama-cp311" in build and "- name: colorama-cp311" in runtime

    def test_backend_marker_excluded_requirement_is_not_emitted(self, tmp_path):
        y, _, _ = self._emit(tmp_path)
        assert "tomli-cp311" not in y  # python_version < 3.11

    def test_interpreter_is_a_host_tool(self, tmp_path):
        # pip and the compiler run ON the builder, not in the target prefix.
        y, _, _ = self._emit(tmp_path)
        assert "host_tools:\n    - python311" in y

    def test_no_manylinux_floor_for_a_source_build(self, tmp_path):
        # manylinux_min pins the glibc floor of a DOWNLOADED wheel.
        y, _, _ = self._emit(tmp_path)
        assert "manylinux_min" not in y

    def test_build_matrix_and_scripts(self, tmp_path):
        y, _, d = self._emit(tmp_path)
        assert "- platform: linux\n      script: build.sh" in y
        assert "- platform: windows\n      script: build.ps1" in y
        assert (d / "build.ps1").is_file()
        assert "pip wheel" in (d / "build.ps1").read_text(encoding="utf-8")

    def test_noarch_package_is_one_any_column_plus_windows(self, tmp_path):
        y, _, d = self._emit(tmp_path, kind="pure", wheels=PURE)
        # One `any` entry still covers every POSIX platform in a single build.
        assert "- platform: any\n      script: build.sh" in y
        assert "platform: linux" not in y
        assert "platform: macos" not in y
        # ...but windows gets its own entry and script, because `any` names
        # build.sh and a Windows builder cannot run it. The payload stays
        # noarch; only the recipe learns that one platform needs PowerShell.
        assert "- platform: windows\n      script: build.ps1" in y
        assert (d / "build.ps1").is_file()
        assert "pip wheel" in (d / "build.ps1").read_text(encoding="utf-8")

    def test_build_sh_builds_the_wheel_offline_without_isolation(self, tmp_path):
        _, sh, _ = self._emit(tmp_path)
        assert "pip wheel" in sh
        for flag in ("--no-build-isolation", "--no-deps", "--no-index"):
            assert flag in sh
        assert '--wheel-dir "${WHEELHOUSE}"' in sh
        assert '"${CVC_SOURCE_DIR}"' in sh

    def test_build_sh_installs_into_the_staging_prefix(self, tmp_path):
        _, sh, _ = self._emit(tmp_path)
        assert "pip install" in sh and '--prefix "${CVC_INSTALL_DIR}"' in sh

    def test_build_sh_bridges_the_build_prefix_onto_syspath(self, tmp_path):
        # depends.build lands in CVC_BUILD_PREFIX, which the deps-prefix
        # interpreter does not import from; without the bridge pip falls back to
        # build isolation and dies offline.
        _, sh, _ = self._emit(tmp_path)
        assert "CVC_BUILD_PREFIX" in sh and "PYTHONPATH" in sh

    def test_build_sh_resolves_the_prefix_interpreter(self, tmp_path):
        _, sh, _ = self._emit(tmp_path)
        assert "_common/python-wheel.sh" in sh
        assert "cvc_python_exe" in sh
        assert "env-${CVC_PLATFORM" in sh

    def test_build_sh_verifies_the_import(self, tmp_path):
        _, sh, _ = self._emit(tmp_path)
        # the bridge must be dropped first or the check could import a
        # build-only backend and hide a missing runtime dep
        assert sh.index("unset PYTHONPATH") < sh.index('cvc_python_check "import widget"')

    def test_recipe_records_why_it_is_from_source(self, tmp_path):
        y, _, _ = self._emit(tmp_path)
        assert "FROM SOURCE" in y
        assert "generated by tools/gen_python_recipes.py" in y  # ownership marker


class TestEmitPrebuiltColumnUnchanged:
    """The fallback shape must keep working, and say why it was used."""

    def _emit(self, tmp_path, **kw):
        m = _meta_sdist(mode="wheel", reason="build backend not packaged: hatchling-cp311", **kw)
        gen._emit_column(tmp_path, "widget", m, "311", {"widget": m, "colorama": {}}, {})
        d = tmp_path / "widget-cp311"
        return (d / "recipe.yaml").read_text(encoding="utf-8"), d

    def test_still_emits_a_pinned_python_wheel_recipe(self, tmp_path):
        y, _ = self._emit(tmp_path)
        assert "type: python_wheel" in y and "artifacts:" in y
        assert "linux-x86_64:" in y
        assert "strip_components" not in y

    def test_records_the_fallback_reason_in_the_recipe(self, tmp_path):
        y, _ = self._emit(tmp_path)
        assert "NOT built from source" in y and "hatchling-cp311" in y

    def test_no_backend_edges_on_a_prebuilt_column(self, tmp_path):
        # Nothing is compiled, so there is no PEP-517 backend to provision.
        y, _ = self._emit(tmp_path)
        assert "cython-cp311" not in y and "setuptools-cp311" not in y

    def test_prebuilt_column_keeps_the_manylinux_floor(self, tmp_path):
        y, _ = self._emit(tmp_path)
        assert "manylinux_min: manylinux_2_28" in y


class TestResolveSourceModes:
    """The whole-run pass: annotate modes and collect blockers."""

    def _run(self, tmp_path, monkeypatch, requires, pure_policy="sdist", forced="auto", **kw):
        monkeypatch.setattr(gen, "sdist_build_requires", lambda sd, cache: requires)
        meta = {"widget": _meta_sdist(**kw)}
        meta["widget"].pop("mode")
        blockers = gen.resolve_source_modes(
            meta,
            {"widget": ["311"]},
            out=tmp_path,
            interps=["311"],
            cache=tmp_path / "cache",
            pure_policy=pure_policy,
            forced=forced,
            platforms=["linux"],
        )
        return meta["widget"], blockers

    def test_packaged_backend_selects_sdist(self, tmp_path, monkeypatch):
        (tmp_path / "setuptools-cp311").mkdir()
        m, blockers = self._run(tmp_path, monkeypatch, ["setuptools"])
        assert m["mode"] == "sdist" and blockers == {}
        assert m["platforms"] == ["linux"]

    def test_unpackaged_backend_blocks_and_is_reported(self, tmp_path, monkeypatch):
        m, blockers = self._run(tmp_path, monkeypatch, ["hatchling"])
        assert m["mode"] == "wheel"
        assert blockers == {"hatchling-cp311": ["widget"]}

    def test_unreadable_sdist_falls_back_instead_of_crashing(self, tmp_path, monkeypatch):
        def _boom(sd, cache):
            raise OSError("truncated tarball")

        monkeypatch.setattr(gen, "sdist_build_requires", _boom)
        meta = {"widget": _meta_sdist()}
        meta["widget"].pop("mode")
        gen.resolve_source_modes(
            meta,
            {"widget": ["311"]},
            out=tmp_path,
            interps=["311"],
            cache=tmp_path,
            pure_policy="sdist",
            forced="auto",
            platforms=["linux"],
        )
        assert meta["widget"]["mode"] == "wheel"
        assert "truncated tarball" in meta["widget"]["reason"]

    def test_prebuilt_only_package_never_downloads_its_sdist(self, tmp_path, monkeypatch):
        def _boom(sd, cache):  # pragma: no cover - must not be called
            raise AssertionError("must not inspect a CUDA redistributable's sdist")

        monkeypatch.setattr(gen, "sdist_build_requires", _boom)
        meta = {"torch": _meta_sdist()}
        meta["torch"].pop("mode")
        gen.resolve_source_modes(
            meta,
            {"torch": ["311"]},
            out=tmp_path,
            interps=["311"],
            cache=tmp_path,
            pure_policy="sdist",
            forced="auto",
            platforms=["linux"],
        )
        assert meta["torch"]["mode"] == "wheel"
