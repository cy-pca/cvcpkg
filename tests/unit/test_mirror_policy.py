"""Tests for the selective-mirroring policy (roadmap Phase 12, increment 1)."""

from __future__ import annotations

import pytest

from cvcpkg.server.mirror_policy import (
    EvictionCandidate,
    MirrorPolicy,
    select_evictions,
)


class TestDecide:
    def test_default_mirrors_everything(self):
        p = MirrorPolicy()
        assert p.decide(name="boost", platform="linux", size_bytes=10**9).mirror is True
        assert p.is_active() is False

    def test_denylist_blocks(self):
        p = MirrorPolicy(exclude=frozenset({"qt6", "vtk"}))
        d = p.decide(name="qt6", platform="linux")
        assert d.mirror is False
        assert "EXCLUDE" in d.reason
        assert p.decide(name="boost").mirror is True

    def test_allowlist_restricts(self):
        p = MirrorPolicy(include=frozenset({"boost", "zlib"}))
        assert p.decide(name="boost").mirror is True
        d = p.decide(name="qt6")
        assert d.mirror is False
        assert "INCLUDE" in d.reason

    def test_denylist_wins_over_allowlist(self):
        # A broad allowlist can still carve out a specific package via deny.
        p = MirrorPolicy(include=frozenset({"boost", "qt6"}), exclude=frozenset({"qt6"}))
        assert p.decide(name="boost").mirror is True
        assert p.decide(name="qt6").mirror is False

    def test_platform_allowlist(self):
        p = MirrorPolicy(platforms=frozenset({"linux", "windows"}))
        assert p.decide(name="boost", platform="linux").mirror is True
        assert p.decide(name="boost", platform="macos").mirror is False
        # Empty platform on the bundle is not filtered out by the platform rule.
        assert p.decide(name="boost", platform="").mirror is True

    def test_size_cap(self):
        p = MirrorPolicy(max_package_bytes=1000)
        assert p.decide(name="boost", size_bytes=999).mirror is True
        assert p.decide(name="boost", size_bytes=1000).mirror is True
        d = p.decide(name="boost", size_bytes=1001)
        assert d.mirror is False
        assert "cap" in d.reason

    def test_combined_order_deny_first(self):
        p = MirrorPolicy(
            include=frozenset({"boost"}),
            exclude=frozenset({"boost"}),
            platforms=frozenset({"linux"}),
            max_package_bytes=10,
        )
        # deny is evaluated first regardless of other rules
        assert p.decide(name="boost", platform="linux", size_bytes=5).mirror is False

    def test_is_active(self):
        assert MirrorPolicy().is_active() is False
        assert MirrorPolicy(include=frozenset({"x"})).is_active() is True
        assert MirrorPolicy(exclude=frozenset({"x"})).is_active() is True
        assert MirrorPolicy(platforms=frozenset({"linux"})).is_active() is True
        assert MirrorPolicy(max_package_bytes=1).is_active() is True


class TestFromEnv:
    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        for var in (
            "CVCPKG_POPULATE_INCLUDE",
            "CVCPKG_POPULATE_EXCLUDE",
            "CVCPKG_POPULATE_PLATFORMS",
            "CVCPKG_POPULATE_MAX_PACKAGE_BYTES",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_empty_env(self):
        p = MirrorPolicy.from_env(default_max_bytes=42)
        assert p.include == frozenset()
        assert p.exclude == frozenset()
        assert p.platforms == frozenset()
        assert p.max_package_bytes == 42  # falls back to the default

    def test_parses_csv(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_POPULATE_INCLUDE", "boost, zlib ,hdf5")
        monkeypatch.setenv("CVCPKG_POPULATE_EXCLUDE", "qt6,vtk")
        monkeypatch.setenv("CVCPKG_POPULATE_PLATFORMS", "linux,windows")
        monkeypatch.setenv("CVCPKG_POPULATE_MAX_PACKAGE_BYTES", "500000000")
        p = MirrorPolicy.from_env(default_max_bytes=10)
        assert p.include == frozenset({"boost", "zlib", "hdf5"})
        assert p.exclude == frozenset({"qt6", "vtk"})
        assert p.platforms == frozenset({"linux", "windows"})
        assert p.max_package_bytes == 500000000  # explicit env overrides default

    def test_max_bytes_default_when_unset(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_POPULATE_INCLUDE", "boost")
        p = MirrorPolicy.from_env(default_max_bytes=123)
        assert p.max_package_bytes == 123


class TestSelectEvictions:
    def _c(self, key, size, downloads):
        return EvictionCandidate(key=key, size_bytes=size, downloads=downloads)

    def test_under_budget_evicts_nothing(self):
        cands = [self._c("a", 100, 0), self._c("b", 100, 5)]
        assert select_evictions(cands, budget_bytes=1000) == []

    def test_zero_budget_is_unbounded(self):
        cands = [self._c("a", 100, 0)]
        assert select_evictions(cands, budget_bytes=0) == []
        assert select_evictions(cands, budget_bytes=-1) == []

    def test_evicts_least_downloaded_first(self):
        cands = [
            self._c("popular", 100, 50),
            self._c("rare", 100, 1),
            self._c("medium", 100, 10),
        ]
        # total 300, budget 250 -> evict one; the rarest goes first.
        evicted = select_evictions(cands, budget_bytes=250)
        assert [c.key for c in evicted] == ["rare"]

    def test_tie_breaks_largest_first(self):
        # equal downloads -> evict the larger (frees more per eviction)
        cands = [self._c("small", 100, 0), self._c("big", 300, 0)]
        evicted = select_evictions(cands, budget_bytes=100)
        assert [c.key for c in evicted] == ["big"]

    def test_evicts_until_under_budget(self):
        cands = [
            self._c("a", 100, 1),
            self._c("b", 100, 2),
            self._c("c", 100, 3),
            self._c("d", 100, 4),
        ]
        # total 400, budget 150 -> must evict until <=150 (evict 3: a,b,c).
        evicted = select_evictions(cands, budget_bytes=150)
        assert sorted(c.key for c in evicted) == ["a", "b", "c"]
        remaining = 400 - sum(c.size_bytes for c in evicted)
        assert remaining <= 150

    def test_keeps_most_downloaded_within_budget(self):
        cands = [
            self._c("keep", 100, 100),
            self._c("drop1", 100, 1),
            self._c("drop2", 100, 2),
        ]
        evicted = select_evictions(cands, budget_bytes=100)
        assert "keep" not in [c.key for c in evicted]
        assert sorted(c.key for c in evicted) == ["drop1", "drop2"]
