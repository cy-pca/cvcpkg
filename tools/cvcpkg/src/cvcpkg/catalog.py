"""Fetch and parse the libcvc-deps bundle catalog."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from cvcpkg.errors import CatalogError, IntegrityError
from cvcpkg.manifest import CatalogEntry, Dependency, ReleaseIndex

DEFAULT_CATALOG_URL = "https://transfix.github.io/libcvc-deps/catalog/latest.yaml"


_MAX_CATALOG_BYTES = 50 * 1024 * 1024  # 50 MB safety limit


def _fetch_url(url: str, *, max_bytes: int = _MAX_CATALOG_BYTES) -> bytes:
    """Fetch a URL and return the raw bytes.

    Raises :class:`CatalogError` if the response exceeds *max_bytes*.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — trusted URL
            length = resp.headers.get("Content-Length")
            if length is not None and int(length) > max_bytes:
                raise CatalogError(
                    f"catalog at {url} is {int(length)} bytes, exceeds {max_bytes} limit"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(1 << 16)  # 64 KB
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise CatalogError(f"catalog at {url} exceeds {max_bytes} byte limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.URLError as e:
        raise CatalogError(f"failed to fetch {url}: {e}") from e


def fetch_catalog(
    url: str = "",
    *,
    cache_dir: Path | None = None,
    expected_sha256: str = "",
) -> dict:
    """Fetch a catalog YAML and return it as a dict.

    If *cache_dir* is given, cache the raw bytes under
    ``<cache_dir>/catalog/<sha256>.yaml``.
    """
    url = url or os.environ.get("CVCPKG_CATALOG_URL", DEFAULT_CATALOG_URL)

    data = _fetch_url(url)
    actual_sha = hashlib.sha256(data).hexdigest()

    if expected_sha256 and actual_sha != expected_sha256:
        raise IntegrityError(
            f"catalog sha256 mismatch: expected {expected_sha256}, got {actual_sha}"
        )

    if cache_dir:
        cat_cache = cache_dir / "catalog"
        cat_cache.mkdir(parents=True, exist_ok=True)
        (cat_cache / f"{actual_sha}.yaml").write_bytes(data)

    return yaml.safe_load(data)


def load_catalog_from_file(path: str | Path) -> dict:
    """Load a catalog YAML from a local file."""
    with open(path) as f:
        return yaml.safe_load(f)


def catalog_entries(
    catalog: dict,
    *,
    platform: str = "",
    arch: str = "",
    build_type: str = "",
    link: str = "",
) -> list[CatalogEntry]:
    """Extract CatalogEntry objects from a catalog dict, optionally filtered."""
    entries: list[CatalogEntry] = []
    for b in catalog.get("bundles", []):
        if platform and b.get("platform", "") != platform:
            continue
        if arch and b.get("arch", "") != arch:
            continue
        if build_type and b.get("build_type", "") != build_type:
            continue
        if link and b.get("link", "") != link:
            continue
        entries.append(
            CatalogEntry(
                name=b["name"],
                version=b["version"],
                upstream_version=b.get("upstream_version", ""),
                cvc_revision=b.get("cvc_revision", 1),
                platform=b.get("platform", ""),
                arch=b.get("arch", ""),
                build_type=b.get("build_type", ""),
                link=b.get("link", ""),
                sha256=b.get("sha256", ""),
                size_bytes=b.get("size_bytes", 0),
                archive_url=b.get("archive_url", ""),
                source_release=b.get("source_release", ""),
                required_deps=[
                    Dependency(name=d["name"], version=d.get("version", ""))
                    for d in b.get("required_deps", [])
                ],
            )
        )
    return entries
