"""The builder installs a dependency version its recipes allow, not just the newest."""

from __future__ import annotations

import pytest

from cvcpkg.cli._builder import (
    _apply_dep_pins,
    _dep_version_specs,
    _newest_first,
    _pick_dep_variant,
)


def _row(version, platform="any", arch="noarch"):
    return {
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": "release",
        "link": "shared",
    }


def _newest(*versions, **kw):
    return sorted((_row(v, **kw) for v in versions), key=_newest_first, reverse=True)


def test_a_pin_selects_the_pinned_source_package_over_a_newer_one():
    # 2026-10-22, jobs 22674-22677: scipy-cp3XX pin scipy-src ==1.17.1, the
    # catalog also had an orphan 1.18.0, and every job installed 1.18.0.
    pkgs = _newest("1.18.0+cvc.1", "1.17.1+cvc.1")
    pins = [("==1.17.1", "scipy-cp311")]
    chosen = _pick_dep_variant(
        _apply_dep_pins("scipy-src", pkgs, pins), "linux", "x86_64", "release", "shared"
    )
    assert chosen["version"] == "1.17.1+cvc.1"


def test_without_a_pin_the_newest_row_still_wins():
    pkgs = _newest("1.18.0+cvc.1", "1.17.1+cvc.1")
    assert _apply_dep_pins("scipy-src", pkgs, []) is pkgs
    assert _pick_dep_variant(pkgs, "linux", "x86_64", "release", "shared")["version"] == (
        "1.18.0+cvc.1"
    )


def test_nothing_satisfying_the_pin_fails_and_says_who_pinned_what():
    pkgs = _newest("1.18.0+cvc.1")
    with pytest.raises(RuntimeError) as err:
        _apply_dep_pins("scipy-src", pkgs, [("==1.17.1", "scipy-cp312")])
    msg = str(err.value)
    assert "scipy-src" in msg and "==1.17.1 (from scipy-cp312)" in msg and "1.18.0+cvc.1" in msg


def test_an_unknown_dep_is_left_to_the_caller():
    assert _apply_dep_pins("nope", [], [("==1.0", "x")]) == []


def test_a_range_keeps_every_satisfying_row_newest_first():
    pkgs = _newest("0.3.28+cvc.10", "0.3.27+cvc.1", "0.3.29+cvc.1", platform="linux", arch="x86_64")
    kept = _apply_dep_pins("openblas", pkgs, [(">=0.3.28", "numpy-cp313")])
    assert [p["version"] for p in kept] == ["0.3.29+cvc.1", "0.3.28+cvc.10"]


def test_an_exact_pin_that_names_a_revision_holds_it():
    pkgs = _newest("8.3+cvc.1", "8.3+cvc.2", "8.3+cvc.3")
    kept = _apply_dep_pins("readline", pkgs, [("==8.3+cvc.2", "libpq")])
    assert [p["version"] for p in kept] == ["8.3+cvc.2"]


def test_a_prerelease_style_upstream_version_matches_its_pin():
    # apsw 3.49.1.0 is carried as 3.49.1-0 (four-part versions do not parse).
    pkgs = _newest("3.49.1-0+cvc.1", platform="linux", arch="x86_64")
    assert _apply_dep_pins("apsw-src", pkgs, [("==3.49.1-0", "apsw-cp313")]) == pkgs


def test_pins_from_two_recipes_must_both_hold():
    pkgs = _newest("2.0.0+cvc.1", "1.5.0+cvc.1", "1.2.0+cvc.1")
    kept = _apply_dep_pins("x", pkgs, [(">=1.2", "a"), ("<2.0", "b")])
    assert [p["version"] for p in kept] == ["1.5.0+cvc.1", "1.2.0+cvc.1"]
    with pytest.raises(RuntimeError, match=r"==1\.2\.0 \(from a\), ==2\.0\.0 \(from b\)"):
        _apply_dep_pins("x", pkgs, [("==1.2.0", "a"), ("==2.0.0", "b")])


def test_dep_version_specs_reads_versions_and_honours_platform_scopes():
    depends = {
        "build": [
            {"name": "scipy-src", "version": "==1.17.1"},
            "python313",
            {"name": "gfortran", "platforms": ["linux"]},
            {"name": "mingw-w64-gcc", "version": ">=13", "platforms": ["windows"]},
        ],
        "runtime": [{"name": "openblas", "version": ">=0.3.28"}],
        "host_tools": ["patchelf"],
    }
    assert _dep_version_specs(depends, "linux") == {
        "scipy-src": ["==1.17.1"],
        "openblas": [">=0.3.28"],
    }
    assert _dep_version_specs(depends, "windows")["mingw-w64-gcc"] == [">=13"]
    assert _dep_version_specs(None, "linux") == {}
