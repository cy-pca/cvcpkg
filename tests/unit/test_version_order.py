"""Rigorous ordering tests for ``semver.version_sort_key``.

cvcpkg encodes a packaging revision as ``+cvc.N`` build metadata, which SemVer
says MUST NOT affect precedence -- so a naive ``sorted`` ties on it and lets
input order pick the winner.  That is how a libpq build installed readline
``8.3+cvc.1`` (broken: ``libreadline.so`` with ``NEEDED=[libc.so.6]`` and
undefined ``tputs``) instead of the fixed ``8.3+cvc.2``.  Several upstream
version strings (``openssh 10.4p1``, ``x264 0.164.stable``, ``jam r1beta5``,
``llvm-cbe 0.0.0+git.<sha>``) are not valid SemVer at all and made a bare
``Version.parse`` in a sort key raise.

The two failure classes need DIFFERENT tests, and neither subsumes the other:

  * A **tie** decided by input order is caught only by permutation invariance
    (T1) -- but permutation invariance does NOT catch a lexical bug, because a
    lexical sort is total and deterministic, hence perfectly permutation
    invariant while still answering ``cvc.10 < cvc.9``.
  * The **lexical / double-digit** bug is caught only by literal example
    expectations (T2).

The pre-existing suite had a good order-independence test and still shipped the
``cvcpkg upgrade openssh 10.4p1 -> 9.9p1`` downgrade for exactly this reason.
So both are mandatory here.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from cvcpkg.builder import list_recipes
from cvcpkg.semver import Version, satisfies, version_sort_key


def _newest(xs):
    return max(xs, key=version_sort_key)


def _sorted(xs):
    return sorted(xs, key=version_sort_key)


# The real recipe corpus, loaded from disk so the test grows with the repo
# rather than going stale the day recipe #141 is added (which is exactly how
# haiku-image/jam appeared after the incident and how the "SEVEN unparseable"
# count drifted).  Cached at module load.
def _recipes_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "recipes"
        if cand.is_dir() and (cand / "_common").exists():
            return cand
    pytest.skip("recipes/ tree not found")


_ALL_FULL_VERSIONS = [
    f"{r.upstream_version}+cvc.{r.cvc_revision}" for r in list_recipes(_recipes_dir())
]


class TestT1PermutationInvariance:
    """For EVERY permutation of a candidate list the entire sorted output is
    identical -- so no tie anywhere is ever decided by input order.

    The oracle is "all permutations agree"; it never references the ordering
    rule, so this cannot be self-fulfilling.
    """

    # Deliberately hostile: parseable + unparseable + prereleases + a
    # double-digit revision + a double-digit upstream component, several tied
    # on their SemVer core so only +cvc.N or the raw-string tiebreak separates
    # them.
    HOSTILE = [
        "8.3+cvc.1",
        "8.3+cvc.2",
        "10.4p1+cvc.1",
        "9.9p1+cvc.1",
        "0.164.stable+cvc.2",
        "0.164.stable+cvc.10",
        "1.0.0-rc.9",
        "1.0.0-rc.10",
        "1.0.0",
        "0.0.0+git.2c21c57+cvc.1",
        "1.10.0+cvc.1",
    ]

    def test_full_order_is_permutation_invariant(self):
        # itertools over a 7-subset (5040 perms) -- exhaustive, not sampled.
        subset = self.HOSTILE[:7]
        orders = {tuple(_sorted(list(p))) for p in itertools.permutations(subset)}
        assert len(orders) == 1, f"input order changed the result: {len(orders)} distinct orders"

    def test_pick_is_permutation_invariant_including_mid_list(self):
        # Assert the WHOLE order, not just [0]: a mid-list tie that _backtrack
        # walks into on the second candidate would pass a [0]-only check.
        base = _sorted(self.HOSTILE)
        for _ in range(2000):
            import random

            shuffled = self.HOSTILE[:]
            random.Random(_).shuffle(shuffled)
            assert _sorted(shuffled) == base

    def test_a_lexical_sort_would_fail_this(self):
        # Guard-rail on the guard: prove a lexical sort really is permutation
        # invariant (so T1 alone is not enough -- hence T2 exists).  A lexical
        # sort is deterministic, so all permutations agree; it just answers
        # wrong.  This documents WHY T2 is mandatory.
        orders = {tuple(sorted(list(p))) for p in itertools.permutations(self.HOSTILE[:7])}
        assert len(orders) == 1  # lexical is permutation-invariant...
        assert sorted(["8.3+cvc.10", "8.3+cvc.9"]) == ["8.3+cvc.10", "8.3+cvc.9"]  # ...and wrong


class TestT2NumericNotLexical:
    """Literal expected values -- the only thing that catches ``10 < 9``.

    Any assertion of the form ``sorted(x, key=K)[0] == max(x, key=K)`` would be
    vacuous here; every expectation below is a hand-written literal.
    """

    @pytest.mark.parametrize(
        "lo,hi",
        [
            # the +cvc.N revision, parseable and unparseable base
            ("1.3.1+cvc.9", "1.3.1+cvc.10"),
            ("10.4p1+cvc.9", "10.4p1+cvc.10"),
            ("0.164.stable+cvc.2", "0.164.stable+cvc.10"),
            # the upstream component itself rolling 9 -> 10
            ("9.9p1+cvc.1", "10.4p1+cvc.1"),  # the live openssh downgrade
            ("10.9.0.0+cvc.1", "10.10.0.0+cvc.1"),  # openssh-win
            ("1.9.0+cvc.1", "1.10.0+cvc.1"),
            # prerelease identifiers compare numerically (SemVer 11.4.1)
            ("1.0.0-rc.9", "1.0.0-rc.10"),
            ("1.0.0-beta.2", "1.0.0-beta.11"),
            # release outranks its prereleases regardless of +cvc.N
            ("1.0.0-rc.1+cvc.9", "1.0.0+cvc.1"),
            # parseable always outranks unparseable
            ("10.4p1+cvc.1", "9.9.0+cvc.1"),
        ],
    )
    def test_lo_is_older_than_hi(self, lo, hi):
        assert version_sort_key(lo) < version_sort_key(hi)
        assert _newest([lo, hi]) == hi
        assert _newest([hi, lo]) == hi  # and independent of input order


class TestT3RealCorpusNeverRaises:
    """Totality and no-raise over the versions cvcpkg actually mints.

    Asserts only no-raise / comparability / distinctness -- NOT an order
    derived from the key, which would be circular.
    """

    def test_no_full_version_raises(self):
        for fv in _ALL_FULL_VERSIONS:
            version_sort_key(fv)  # must not raise

    def test_whole_corpus_is_mutually_comparable(self):
        # A single sorted() over every minted version proves no two keys are
        # incomparable (which would raise TypeError mid-sort).
        _sorted(_ALL_FULL_VERSIONS)

    def test_the_known_unparseable_versions_are_present_and_safe(self):
        # These are real recipes whose full_version is NOT valid SemVer; a bare
        # Version.parse in a sort key raised on them (the `cvcpkg info openssh`
        # traceback).  Assert the key handles each without raising and ranks it
        # below a well-formed version.
        known_unparseable = [
            "10.4p1+cvc.1",  # openssh
            "0.164.stable+cvc.1",  # x264
            "0.0.0+git.2c21c57+cvc.1",  # llvm-cbe (two '+')
            "10.0.0.0+cvc.2",  # openssh-win (4 numeric components)
            "r1beta5+cvc.1",  # jam / haiku-image
        ]
        for v in known_unparseable:
            with pytest.raises(ValueError):
                Version.parse(v)  # confirms it really is unparseable
            assert version_sort_key(v)[0] == 0  # rank 0
            assert version_sort_key(v) < version_sort_key("1.0.0+cvc.1")


class TestT4ReadlineIncident:
    """The exact production shape: the DB returns rows published_at DESC, and
    cvc.1 (id 903) was published 2s AFTER cvc.2 (id 895) -- so the input order
    is [cvc.1, cvc.2].  A fixture ordered [cvc.2, cvc.1] would pass on the buggy
    code, so BOTH orders are asserted.
    """

    def test_picks_cvc2_regardless_of_input_order(self):
        for order in (["8.3+cvc.1", "8.3+cvc.2"], ["8.3+cvc.2", "8.3+cvc.1"]):
            assert _newest(order) == "8.3+cvc.2"
            assert _sorted(order) == ["8.3+cvc.1", "8.3+cvc.2"]

    def test_pinning_cvc2_rejects_the_broken_cvc1(self):
        # The remedy an operator reaches for after this incident.
        assert not satisfies("8.3+cvc.1", "==8.3+cvc.2")
        assert satisfies("8.3+cvc.2", "==8.3+cvc.2")


class TestKeyProperties:
    """The invariants the whole design rests on."""

    def test_key_is_total_distinct_strings_never_tie(self):
        # The raw string is the final tiebreak, so two DIFFERENT version strings
        # can never produce equal keys and fall back to input order -- this is
        # the property that kills the bug class, not just the readline instance.
        seen: dict[tuple, str] = {}
        pool = _ALL_FULL_VERSIONS + [
            "8.3+cvc.1",
            "8.3+cvc.2",
            "2025.07+cvc.1",
            "2025.7+cvc.1",
        ]
        for v in pool:
            k = version_sort_key(v)
            if k in seen and seen[k] != v:
                pytest.fail(f"distinct versions {seen[k]!r} and {v!r} share a key")
            seen[k] = v

    def test_revision_read_from_string_not_argument(self):
        # Two call sites passing different cvc_revision args must still agree,
        # because the +cvc.N in the string wins.
        assert version_sort_key("8.3+cvc.2", 1) == version_sort_key("8.3+cvc.2", 99)
        assert version_sort_key("8.3+cvc.2") == version_sort_key("8.3+cvc.2", 5)

    def test_argument_is_fallback_only_when_string_lacks_cvc(self):
        # catalog rows omit the +cvc.N suffix; the argument supplies it there.
        assert version_sort_key("8.3", 2) > version_sort_key("8.3", 1)

    def test_ascending_newest_via_max_or_reverse(self):
        xs = ["8.3+cvc.1", "8.3+cvc.2", "8.3+cvc.10"]
        assert _newest(xs) == "8.3+cvc.10"
        assert sorted(xs, key=version_sort_key, reverse=True)[0] == "8.3+cvc.10"

    def test_empty_and_whitespace_do_not_raise(self):
        version_sort_key("")
        version_sort_key("   ")
