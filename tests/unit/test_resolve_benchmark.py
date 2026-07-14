"""Performance guard for the dependency resolver on large catalogs.

Builds a synthetic catalog of many components (each with several versions
and a chain of transitive deps) and asserts the resolver picks a full graph
well under a generous wall-clock bound.  This is a regression guard, not a
micro-benchmark — the bound is loose enough to be stable in CI but tight
enough to catch an accidental exponential blow-up.
"""

from __future__ import annotations

import time

from cvcpkg.manifest import CatalogEntry, ComponentReq, Dependency
from cvcpkg.resolver import resolve


def _entry(name: str, version: str, deps: list[Dependency] | None = None) -> CatalogEntry:
    return CatalogEntry(
        name=name,
        version=version,
        upstream_version=version.split("+")[0],
        cvc_revision=1,
        platform="linux",
        arch="x86_64",
        build_type="release",
        link="shared",
        sha256="",
        size_bytes=0,
        archive_url=f"https://example.test/{name}-{version}.tar.gz",
        source_release="",
        required_deps=deps or [],
    )


def _large_catalog(n_components: int, versions_each: int) -> dict[str, list[CatalogEntry]]:
    """A chain: pkg{i} depends on pkg{i-1}; each has several versions."""
    catalog: dict[str, list[CatalogEntry]] = {}
    for i in range(n_components):
        name = f"pkg{i}"
        deps = [Dependency(name=f"pkg{i - 1}", version="^1.0")] if i > 0 else []
        catalog[name] = [_entry(name, f"1.0.{v}+cvc.1", deps) for v in range(versions_each)]
    return catalog


def test_resolve_large_chain_catalog_is_fast():
    n = 400
    catalog = _large_catalog(n, versions_each=6)
    # Requesting the tail pulls the entire transitive chain.
    reqs = [ComponentReq(name=f"pkg{n - 1}")]

    start = time.perf_counter()
    result = resolve(reqs, catalog)
    elapsed = time.perf_counter() - start

    # The whole chain resolved...
    assert len(result.picked) == n
    # ...and each pick is the highest available version.
    assert result.picked[f"pkg{n - 1}"].version == "1.0.5+cvc.1"
    # ...comfortably under the regression bound.
    assert elapsed < 5.0, f"resolve took {elapsed:.2f}s (>5s regression bound)"
