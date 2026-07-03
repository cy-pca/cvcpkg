"""Tests for cvcpkg.resolver — backtracking dependency resolver."""

import pytest

from cvcpkg.errors import ResolveError
from cvcpkg.manifest import CatalogEntry, ComponentReq, Dependency
from cvcpkg.resolver import resolve


def _entry(name: str, version: str, deps: list[Dependency] | None = None) -> CatalogEntry:
    """Helper: build a minimal CatalogEntry."""
    return CatalogEntry(
        name=name,
        version=version,
        upstream_version=version.split("+")[0],
        cvc_revision=1,
        platform="linux",
        arch="x86_64",
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
