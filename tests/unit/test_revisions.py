"""Unit tests for cvcpkg.revisions and the pack --bump command surface.

Covers the pure revision arithmetic, the published-revision server query
(with a mocked httpx client), the compute_pack_revision glue, and the
next-revision / cascade-bump CLI commands.  No network or server required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cvcpkg.revisions import (
    base_version,
    compute_pack_revision,
    fetch_published_revisions,
    next_revision,
    revision_of,
)

# ── Fake httpx client ───────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _fake_client(packages, status: int = 200, boom: bool = False):
    """Return a class usable as a drop-in for httpx.Client."""

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            if boom:
                raise RuntimeError("connection refused")
            return _FakeResp(status, {"packages": packages})

    return _Client


def _pkg(
    version, *, platform="linux", arch="x86_64", build_type="release", link="shared", org="cvc"
):
    return {
        "name": "libcvc",
        "version": version,
        "platform": platform,
        "arch": arch,
        "build_type": build_type,
        "link": link,
        "org": org,
    }


# ── revision_of / base_version ──────────────────────────────────


class TestRevisionParsing:
    @pytest.mark.parametrize(
        "version,expected",
        [
            ("3.2.4+cvc.5", 5),
            ("3.2.4+cvc.12", 12),
            ("1.0.0+cvc.1", 1),
            ("3.2.4", 0),
            ("", 0),
            ("10.4p1", 0),  # non-semver upstream, no suffix
        ],
    )
    def test_revision_of(self, version, expected):
        assert revision_of(version) == expected

    @pytest.mark.parametrize(
        "version,expected",
        [
            ("3.2.4+cvc.5", "3.2.4"),
            ("3.2.4", "3.2.4"),
            ("1.2.3-rc1+cvc.2", "1.2.3-rc1"),
            ("", ""),
        ],
    )
    def test_base_version(self, version, expected):
        assert base_version(version) == expected


# ── next_revision ───────────────────────────────────────────────


class TestNextRevision:
    def test_first_publish_uses_recipe_floor(self):
        # Nothing published -> land at the recipe's own revision, unbumped.
        assert next_revision(1, []) == 1
        assert next_revision(3, []) == 3

    def test_republish_goes_one_above_published(self):
        assert next_revision(2, [2]) == 3
        assert next_revision(1, [1]) == 2

    def test_honors_recipe_floor_when_ahead(self):
        # Recipe manually bumped to 3 but only 2 is published -> pack 3 (fresh).
        assert next_revision(3, [2]) == 3
        # Floor below published -> one above published.
        assert next_revision(2, [4]) == 5

    def test_uses_highest_published(self):
        assert next_revision(1, [1, 2, 4, 3]) == 5

    def test_zero_floor(self):
        assert next_revision(0, []) == 0
        assert next_revision(0, [0]) == 1


# ── fetch_published_revisions ───────────────────────────────────


class TestFetchPublishedRevisions:
    def test_scope_name_counts_all_variants(self, monkeypatch):
        pkgs = [
            _pkg("3.2.4+cvc.2", platform="linux"),
            _pkg("3.2.4+cvc.3", platform="macos"),
            _pkg("3.2.4+cvc.1", platform="windows", link="static"),
        ]
        monkeypatch.setattr("httpx.Client", _fake_client(pkgs))
        revs = fetch_published_revisions(
            "libcvc", server="http://x", org="cvc", upstream_version="3.2.4", scope="name"
        )
        assert sorted(revs) == [1, 2, 3]
        assert next_revision(2, revs) == 4

    def test_scope_variant_filters_tuple(self, monkeypatch):
        pkgs = [
            _pkg("3.2.4+cvc.2", platform="linux", build_type="release"),
            _pkg("3.2.4+cvc.9", platform="linux", build_type="debug"),
            _pkg("3.2.4+cvc.7", platform="macos", build_type="release"),
        ]
        monkeypatch.setattr("httpx.Client", _fake_client(pkgs))
        revs = fetch_published_revisions(
            "libcvc",
            server="http://x",
            org="cvc",
            upstream_version="3.2.4",
            platform="linux",
            arch="x86_64",
            build_type="release",
            link="shared",
            scope="variant",
        )
        assert revs == [2]  # only the linux/release/shared/x86_64 entry

    def test_upstream_version_filter_excludes_other_releases(self, monkeypatch):
        pkgs = [
            _pkg("3.2.4+cvc.5"),  # current upstream
            _pkg("3.1.0+cvc.9"),  # older upstream — must not raise our floor
        ]
        monkeypatch.setattr("httpx.Client", _fake_client(pkgs))
        revs = fetch_published_revisions(
            "libcvc", server="http://x", org="cvc", upstream_version="3.2.4"
        )
        assert revs == [5]

    def test_org_filter_excludes_foreign_org(self, monkeypatch):
        pkgs = [
            _pkg("3.2.4+cvc.2", org="cvc"),
            _pkg("3.2.4+cvc.8", org="someoneelse"),
        ]
        monkeypatch.setattr("httpx.Client", _fake_client(pkgs))
        revs = fetch_published_revisions(
            "libcvc", server="http://x", org="cvc", upstream_version="3.2.4"
        )
        assert revs == [2]

    def test_server_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr("httpx.Client", _fake_client([], status=500))
        assert fetch_published_revisions("libcvc", server="http://x") == []

    def test_unreachable_returns_empty(self, monkeypatch):
        monkeypatch.setattr("httpx.Client", _fake_client([], boom=True))
        assert fetch_published_revisions("libcvc", server="http://x") == []


# ── compute_pack_revision ───────────────────────────────────────


class TestComputePackRevision:
    def _recipe(self, name="libcvc", upstream="3.2.4", rev=2):
        return SimpleNamespace(name=name, upstream_version=upstream, cvc_revision=rev)

    def test_no_server_returns_floor(self):
        r = self._recipe(rev=4)
        assert compute_pack_revision(r, server="") == 4

    def test_bumps_above_published(self, monkeypatch):
        monkeypatch.setattr("cvcpkg.revisions.fetch_published_revisions", lambda *a, **k: [2, 3])
        r = self._recipe(rev=2)
        assert compute_pack_revision(r, server="http://x") == 4

    def test_first_publish_keeps_floor(self, monkeypatch):
        monkeypatch.setattr("cvcpkg.revisions.fetch_published_revisions", lambda *a, **k: [])
        r = self._recipe(rev=1)
        assert compute_pack_revision(r, server="http://x") == 1
