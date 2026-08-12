"""Tests for cvcpkg.resolver — backtracking dependency resolver."""

import pytest

from cvcpkg.errors import ResolveError
from cvcpkg.manifest import CatalogEntry, ComponentReq, Dependency
from cvcpkg.resolver import resolve


def _entry(
    name: str,
    version: str,
    deps: list[Dependency] | None = None,
    *,
    platform: str = "linux",
    arch: str = "x86_64",
) -> CatalogEntry:
    """Helper: build a minimal CatalogEntry."""
    return CatalogEntry(
        name=name,
        version=version,
        upstream_version=version.split("+")[0],
        cvc_revision=1,
        platform=platform,
        arch=arch,
        build_type="release",
        link="shared",
        sha256="0" * 64,
        size_bytes=100,
        archive_url=f"https://example.com/{name}-{version}.tar.gz",
        source_release="1.0.0",
        required_deps=deps or [],
    )


class TestResolverBasic:
    def test_single_component_no_deps(self):
        reqs = [ComponentReq(name="zlib")]
        candidates = {"zlib": [_entry("zlib", "1.3.1+cvc.1")]}
        result = resolve(reqs, candidates)
        assert "zlib" in result.picked
        assert result.picked["zlib"].version == "1.3.1+cvc.1"

    def test_picks_highest_version(self):
        reqs = [ComponentReq(name="zlib")]
        candidates = {
            "zlib": [
                _entry("zlib", "1.3.0+cvc.1"),
                _entry("zlib", "1.3.1+cvc.1"),
                _entry("zlib", "1.2.13+cvc.1"),
            ]
        }
        result = resolve(reqs, candidates)
        assert result.picked["zlib"].version == "1.3.1+cvc.1"

    def test_picks_highest_cvc_revision_when_versions_tie(self):
        # Regression: Version.__lt__ ignores +cvc.N build metadata (SemVer),
        # so a naive descending sort was broken by input list order,
        # letting an older/broken cvc.1 bundle win over a fixed cvc.2.
        reqs = [ComponentReq(name="fftw3")]
        candidates = {
            "fftw3": [
                _entry("fftw3", "3.3.10+cvc.1"),
                _entry("fftw3", "3.3.10+cvc.2"),
            ]
        }
        result = resolve(reqs, candidates)
        assert result.picked["fftw3"].version == "3.3.10+cvc.2"

        # Order-independent
        candidates = {
            "fftw3": [
                _entry("fftw3", "3.3.10+cvc.2"),
                _entry("fftw3", "3.3.10+cvc.1"),
            ]
        }
        result = resolve(reqs, candidates)
        assert result.picked["fftw3"].version == "3.3.10+cvc.2"

    def test_concrete_platform_beats_noarch_at_the_same_version(self):
        """A bundle built for this platform wins a tie against a noarch one.

        Both satisfy the host — `platform_matches` lets `any` through on
        purpose — but they are not interchangeable.  A recipe ships both only
        because its noarch build cannot serve every target: for the pure-Python
        columns the noarch bundle installs to lib/pythonX.Y/site-packages while
        Windows needs Lib/site-packages, so picking noarch there puts the files
        where that interpreter never looks.
        """
        reqs = [ComponentReq(name="anyio-cp311")]
        noarch = _entry("anyio-cp311", "4.13.0+cvc.2", platform="any", arch="noarch")
        native = _entry("anyio-cp311", "4.13.0+cvc.2", platform="windows")
        for order in ([noarch, native], [native, noarch]):
            result = resolve(reqs, {"anyio-cp311": list(order)})
            assert result.picked["anyio-cp311"].platform == "windows"

    def test_version_still_dominates_platform_specificity(self):
        """Specificity only breaks ties — it must not pin a host to old content."""
        reqs = [ComponentReq(name="anyio-cp311")]
        candidates = {
            "anyio-cp311": [
                _entry("anyio-cp311", "4.13.0+cvc.1", platform="windows"),
                _entry("anyio-cp311", "4.13.0+cvc.2", platform="any", arch="noarch"),
            ]
        }
        result = resolve(reqs, candidates)
        assert result.picked["anyio-cp311"].version == "4.13.0+cvc.2"
        assert result.picked["anyio-cp311"].platform == "any"

    def test_picks_highest_cvc_revision_under_version_constraint(self):
        reqs = [ComponentReq(name="fftw3", version="==3.3.10")]
        candidates = {
            "fftw3": [
                _entry("fftw3", "3.3.10+cvc.1"),
                _entry("fftw3", "3.3.10+cvc.3"),
                _entry("fftw3", "3.3.10+cvc.2"),
            ]
        }
        result = resolve(reqs, candidates)
        assert result.picked["fftw3"].version == "3.3.10+cvc.3"

    def test_version_constraint(self):
        reqs = [ComponentReq(name="zlib", version="==1.3.0")]
        candidates = {
            "zlib": [
                _entry("zlib", "1.3.0+cvc.1"),
                _entry("zlib", "1.3.1+cvc.1"),
            ]
        }
        result = resolve(reqs, candidates)
        assert result.picked["zlib"].version == "1.3.0+cvc.1"

    def test_exclude(self):
        reqs = [
            ComponentReq(name="zlib"),
            ComponentReq(name="tiff", exclude=True),
        ]
        candidates = {
            "zlib": [_entry("zlib", "1.3.1+cvc.1")],
            "tiff": [_entry("tiff", "4.6.0+cvc.1")],
        }
        result = resolve(reqs, candidates)
        assert "zlib" in result.picked
        assert "tiff" not in result.picked


class TestResolverDeps:
    def test_transitive_dep(self):
        """hdf5 depends on zlib — resolver should pull zlib automatically."""
        reqs = [ComponentReq(name="hdf5")]
        candidates = {
            "hdf5": [
                _entry(
                    "hdf5",
                    "1.14.4+cvc.1",
                    deps=[
                        Dependency(name="zlib", version="^1.3"),
                    ],
                )
            ],
            "zlib": [
                _entry("zlib", "1.3.1+cvc.1"),
                _entry("zlib", "1.2.13+cvc.1"),
            ],
        }
        result = resolve(reqs, candidates)
        assert "hdf5" in result.picked
        assert "zlib" in result.picked
        # Should pick 1.3.1 since ^1.3 excludes 1.2.x
        assert result.picked["zlib"].version == "1.3.1+cvc.1"

    def test_conflict_raises(self):
        """Two deps with incompatible constraints on a third."""
        reqs = [ComponentReq(name="a"), ComponentReq(name="b")]
        candidates = {
            "a": [
                _entry(
                    "a",
                    "1.0.0+cvc.1",
                    deps=[
                        Dependency(name="c", version=">=2.0.0"),
                    ],
                )
            ],
            "b": [
                _entry(
                    "b",
                    "1.0.0+cvc.1",
                    deps=[
                        Dependency(name="c", version="<2.0.0"),
                    ],
                )
            ],
            "c": [
                _entry("c", "1.9.0+cvc.1"),
                _entry("c", "2.1.0+cvc.1"),
            ],
        }
        with pytest.raises(ResolveError):
            resolve(reqs, candidates)

    def test_recommended_preferred(self):
        reqs = [ComponentReq(name="zlib")]
        candidates = {
            "zlib": [
                _entry("zlib", "1.3.0+cvc.1"),
                _entry("zlib", "1.3.1+cvc.1"),
            ]
        }
        result = resolve(reqs, candidates, recommended={"zlib": "1.3.0+cvc.1"})
        assert result.picked["zlib"].version == "1.3.0+cvc.1"

    def test_unparseable_candidate_ignored_for_unrelated_component(self):
        # A malformed version on an unrequested bundle must not sink
        # resolution for components the user actually asked for.
        reqs = [ComponentReq(name="zlib")]
        candidates = {
            "zlib": [_entry("zlib", "1.3.1+cvc.1")],
            "weirdo": [_entry("weirdo", "not-a-version")],
        }
        result = resolve(reqs, candidates)
        assert result.picked["zlib"].version == "1.3.1+cvc.1"


class TestNonSemverVersions:
    """Published bundles with non-semver versions (openssh "10.4p1+cvc.1")
    must stay installable — the resolver offers them after all parseable
    candidates instead of silently dropping them."""

    def test_nonsemver_only_candidate_resolves(self):
        reqs = [ComponentReq(name="openssh")]
        candidates = {"openssh": [_entry("openssh", "10.4p1+cvc.1")]}
        result = resolve(reqs, candidates)
        assert result.picked["openssh"].version == "10.4p1+cvc.1"

    def test_nonsemver_with_parseable_deps(self):
        reqs = [ComponentReq(name="openssh")]
        candidates = {
            "openssh": [
                _entry(
                    "openssh",
                    "10.4p1+cvc.1",
                    [Dependency(name="openssl", version="^3.0")],
                )
            ],
            "openssl": [_entry("openssl", "3.4.1+cvc.3")],
        }
        result = resolve(reqs, candidates)
        assert result.picked["openssh"].version == "10.4p1+cvc.1"
        assert result.picked["openssl"].version == "3.4.1+cvc.3"

    def test_parseable_candidates_preferred(self):
        reqs = [ComponentReq(name="foo")]
        candidates = {"foo": [_entry("foo", "2.0beta+cvc.1"), _entry("foo", "1.9.0+cvc.1")]}
        result = resolve(reqs, candidates)
        assert result.picked["foo"].version == "1.9.0+cvc.1"

    def test_range_constraint_rejects_nonsemver(self):
        reqs = [ComponentReq(name="app")]
        candidates = {
            "app": [
                _entry(
                    "app",
                    "1.0.0+cvc.1",
                    [Dependency(name="openssh", version="^10.0")],
                )
            ],
            "openssh": [_entry("openssh", "10.4p1+cvc.1")],
        }
        with pytest.raises(ResolveError):
            resolve(reqs, candidates)
