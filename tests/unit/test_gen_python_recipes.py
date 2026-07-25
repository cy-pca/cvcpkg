"""Tests for tools/gen_python_recipes.py — the classifier that decides whether a
Python dependency is emitted as a noarch, stable-ABI (abi3), or per-version
C-extension recipe.

The subtle case is the *hybrid*: a stable-ABI package (cryptography, bcrypt,
pynacl) that ALSO ships a free-threaded (`cp313-cp313t`) and/or newer-Python
(`cp314`) per-version wheel. Those must not make it "per-version" for the
interpreters we actually target (3.11/3.12/3.13), or the matrix needlessly
triples and three recipes end up shipping the identical abi3 wheel.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GEN = Path(__file__).resolve().parents[2] / "tools" / "gen_python_recipes.py"
_spec = importlib.util.spec_from_file_location("gen_python_recipes", _GEN)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

INTERPS = ["311", "312", "313"]


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


class TestWheelSelectors:
    def test_abi3_wheel_prefers_manylinux(self):
        w = gen.abi3_wheel_for(CRYPTOGRAPHY, "linux-x86_64")
        assert w is not None and "manylinux" in w["filename"] and "abi3" in w["filename"]

    def test_cext_wheel_exact_non_free_threaded(self):
        w = gen.cext_wheel_for(ASYNCPG, "312", "linux-x86_64")
        assert w is not None and "-cp312-cp312-" in w["filename"]

    def test_cext_wheel_does_not_match_free_threaded_only(self):
        # bcrypt has cp313-cp313t (free-threaded) but no cp313-cp313 — a
        # non-free-threaded 3.13 build must NOT pick up the free-threaded wheel.
        assert gen.cext_wheel_for(BCRYPT, "313", "linux-x86_64") is None

    def test_cext_wheel_ignores_abi3(self):
        # Stable-ABI wheels are handled by abi3_wheel_for; the per-version
        # selector must not fall back to them.
        assert gen.cext_wheel_for(CRYPTOGRAPHY, "312", "linux-x86_64") is None
