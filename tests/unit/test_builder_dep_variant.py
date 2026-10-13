"""The builder picks the dependency variant matching the job's build_type/link."""

from __future__ import annotations

from cvcpkg.cli._builder import _pick_dep_variant


def _row(version, platform="any", arch="noarch", build_type="release", link="shared"):
    return {
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": build_type,
        "link": link,
    }


def test_release_job_skips_a_debug_noarch_row_listed_first():
    # trove-classifiers-cp313 on 2026-10-13: same version, debug row first.
    debug, release = _row("2026.6.1-19+cvc.6", build_type="debug"), _row("2026.6.1-19+cvc.6")
    assert _pick_dep_variant([debug, release], "linux", "x86_64", "release", "shared") is release


def test_debug_job_prefers_the_debug_noarch_row():
    debug, release = _row("1.0+cvc.1", build_type="debug"), _row("1.0+cvc.1")
    assert _pick_dep_variant([release, debug], "linux", "x86_64", "debug", "shared") is debug


def test_falls_back_to_another_build_type_when_that_is_all_there_is():
    debug = _row("1.0+cvc.1", build_type="debug")
    assert _pick_dep_variant([debug], "linux", "x86_64", "release", "shared") is debug


def test_exact_platform_match_beats_everything():
    exact = _row("1.0+cvc.1", "linux", "x86_64")
    other_cfg = _row("1.0+cvc.2", "linux", "x86_64", build_type="debug")
    noarch = _row("1.0+cvc.3")
    assert (
        _pick_dep_variant([noarch, other_cfg, exact], "linux", "x86_64", "release", "shared")
        is exact
    )


def test_platform_row_of_another_build_type_still_beats_noarch():
    # Unchanged order: any platform/arch row before falling back to noarch.
    debug_native = _row("1.0+cvc.1", "linux", "x86_64", build_type="debug")
    noarch = _row("1.0+cvc.1")
    assert (
        _pick_dep_variant([noarch, debug_native], "linux", "x86_64", "release", "shared")
        is debug_native
    )


def test_keeps_the_callers_newest_first_order_within_a_tier():
    newer, older = _row("2.0+cvc.1"), _row("1.0+cvc.1")
    assert _pick_dep_variant([newer, older], "linux", "x86_64", "release", "shared") is newer


def test_link_mode_counts_too():
    static, shared = _row("1.0+cvc.1", link="static"), _row("1.0+cvc.1")
    assert _pick_dep_variant([static, shared], "linux", "x86_64", "release", "shared") is shared


def test_nothing_usable():
    assert (
        _pick_dep_variant(
            [_row("1.0+cvc.1", "macos", "arm64")], "linux", "x86_64", "release", "shared"
        )
        is None
    )
