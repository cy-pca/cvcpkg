# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Pure device-pairing primitives (no database)."""

from __future__ import annotations

import pytest

# pairing.py lives under cvcpkg.server and imports auth -> models -> pydantic, so
# it needs the server extras even though its logic is pure.
pytest.importorskip("pydantic", reason="server extras not installed")

from cvcpkg.server import pairing


def test_user_code_shape_and_alphabet():
    for _ in range(200):
        code = pairing.new_user_code()
        assert len(code) == 9 and code[4] == "-"
        compact = code.replace("-", "")
        assert len(compact) == pairing.USER_CODE_LEN
        assert all(c in pairing.USER_CODE_ALPHABET for c in compact)
        # The ambiguous glyphs must never appear.
        assert not (set(compact) & set("01OILU"))


@pytest.mark.parametrize(
    "raw,expected_len",
    [
        ("7QK4-M2XZ", 8),
        ("7qk4 m2xz", 8),  # lowercase + space
        ("7qk4m2xz", 8),
        ("  7QK4-M2XZ  ", 8),
    ],
)
def test_normalize_accepts_typed_variants(raw, expected_len):
    norm = pairing.normalize_user_code(raw)
    assert norm is not None and len(norm) == expected_len and norm == norm.upper()


@pytest.mark.parametrize("bad", ["", "SHORT", "TOO-LONG-CODE-HERE", "1234-5678"])
def test_normalize_rejects_bad_shapes(bad):
    # '1234-5678' contains '1' which is not in the alphabet, so it drops below 8.
    assert pairing.normalize_user_code(bad) is None


def test_verifier_hash_roundtrip():
    v = pairing.new_verifier()
    h = pairing.hash_verifier(v)
    assert pairing.verifier_matches(v, h)
    assert not pairing.verifier_matches("other", h)
    assert not pairing.verifier_matches(v, "")


def test_user_code_hash_is_normalisation_stable_and_keyed():
    key = b"k" * 32
    assert pairing.hash_user_code(key, "7qk4 m2xz") == pairing.hash_user_code(key, "7QK4-M2XZ")
    assert pairing.hash_user_code(key, "7QK4M2XZ") != pairing.hash_user_code(b"j" * 32, "7QK4M2XZ")


def test_next_interval_backoff():
    assert pairing.next_interval(5, slow_down=False) == 5
    assert pairing.next_interval(5, slow_down=True) == 10
    assert pairing.next_interval(40, slow_down=True) == pairing.MAX_POLL_INTERVAL
    # Never below the floor.
    assert pairing.next_interval(1, slow_down=False) == pairing.MIN_POLL_INTERVAL
