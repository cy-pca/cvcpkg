"""Revision selection: `install <pkg>` must resolve the NEWEST +cvc.N.

Reported symptom: `cvcpkg install openblas` returned 0.3.28+cvc.3 while
+cvc.4 and +cvc.5 were published.  Selection itself turned out to be correct
(version_sort_key re-parses the suffix out of the version string), but it was
correct by luck: CatalogEntry.cvc_revision was defaulted to 1 for every entry
because /v1/catalog carries the suffix in `version` and ships no
`cvc_revision` field.  Any producer that omits the suffix — which the server
catalog is documented as doing — would tie every revision and hand back an
arbitrary one, reproducing exactly the reported symptom.

These pin both halves: the revision is recovered from whichever input carries
it, and selection picks the newest either way.
"""

from __future__ import annotations

import pytest

from cvcpkg.catalog import catalog_entries
from cvcpkg.semver import version_sort_key


def _bundle(version, *, cvc_revision=None, name="openblas"):
    b = {
        "name": name,
        "version": version,
        "platform": "linux",
        "arch": "x86_64",
        "build_type": "release",
        "link": "shared",
        "sha256": "a" * 64,
        "size_bytes": 1,
        "archive_url": f"/v1/download/{name}-{version}.tar.zst",
        "published_at": "2026-01-01T00:00:00+00:00",
    }
    if cvc_revision is not None:
        b["cvc_revision"] = cvc_revision
    return b


def _entries(bundles):
    return catalog_entries(
        {"bundles": bundles},
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
    )


def _pick(entries):
    """What the resolver would choose (newest-first, same key it uses)."""
    return sorted(entries, key=lambda e: version_sort_key(e.version, e.cvc_revision), reverse=True)[
        0
    ]


class TestRevisionRecovery:
    def test_recovered_from_the_version_suffix(self):
        # /v1/catalog's actual shape: suffix in `version`, no cvc_revision.
        es = _entries([_bundle("0.3.28+cvc.3"), _bundle("0.3.28+cvc.5"), _bundle("0.3.28+cvc.1")])
        assert {e.cvc_revision for e in es} == {1, 3, 5}

    def test_explicit_field_wins(self):
        es = _entries([_bundle("0.3.28+cvc.3", cvc_revision=7)])
        assert es[0].cvc_revision == 7

    def test_absent_everywhere_defaults_to_one(self):
        es = _entries([_bundle("0.3.28")])
        assert es[0].cvc_revision == 1


class TestSelection:
    def test_picks_newest_when_suffix_present(self):
        es = _entries(
            [
                _bundle("0.3.28+cvc.3"),
                _bundle("0.3.28+cvc.5"),
                _bundle("0.3.28+cvc.1"),
                _bundle("0.3.28+cvc.4"),
            ]
        )
        assert _pick(es).version == "0.3.28+cvc.5"

    def test_picks_newest_when_only_the_field_carries_the_revision(self):
        # The latent break: identical version strings. Before recovery every
        # entry tied at revision 1 and the winner was whichever happened to be
        # first — the reported stale-revision symptom.
        es = _entries(
            [
                _bundle("0.3.28", cvc_revision=3),
                _bundle("0.3.28", cvc_revision=5),
                _bundle("0.3.28", cvc_revision=1),
            ]
        )
        assert _pick(es).cvc_revision == 5

    def test_input_order_does_not_decide(self):
        newest_first = _entries([_bundle("0.3.28+cvc.5"), _bundle("0.3.28+cvc.3")])
        newest_last = _entries([_bundle("0.3.28+cvc.3"), _bundle("0.3.28+cvc.5")])
        assert _pick(newest_first).version == _pick(newest_last).version == "0.3.28+cvc.5"

    def test_double_digit_revision_beats_single(self):
        # cvc.10 > cvc.9 numerically; lexically it is the other way round.
        es = _entries([_bundle("0.3.28+cvc.9"), _bundle("0.3.28+cvc.10")])
        assert _pick(es).version == "0.3.28+cvc.10"

    @pytest.mark.parametrize("upstream", ["1.2.3", "10.4p1", "2024.11.6"])
    def test_revision_beats_revision_within_one_upstream_version(self, upstream):
        # Including the unparseable-semver case (openssh's "10.4p1"), which
        # must still order by revision among its own kind.
        es = _entries([_bundle(f"{upstream}+cvc.1"), _bundle(f"{upstream}+cvc.4")])
        assert _pick(es).version == f"{upstream}+cvc.4"

    def test_newer_upstream_beats_higher_revision(self):
        # A revision bump must not outrank an actual upstream upgrade.
        es = _entries([_bundle("0.3.28+cvc.9"), _bundle("0.3.29+cvc.1")])
        assert _pick(es).version == "0.3.29+cvc.1"
