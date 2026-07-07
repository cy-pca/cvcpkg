"""Fetch, parse, and generate the libcvc-deps bundle catalog."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from cvcpkg.config import default_catalog_url
from cvcpkg.errors import CatalogError, IntegrityError
from cvcpkg.manifest import CatalogEntry, Dependency

GITHUB_CATALOG_URL = "https://transfix.github.io/libcvc-deps/catalog/latest.yaml"


_MAX_CATALOG_BYTES = 50 * 1024 * 1024  # 50 MB safety limit


def _fetch_url(url: str, *, max_bytes: int = _MAX_CATALOG_BYTES) -> bytes:
    """Fetch a URL and return the raw bytes.

    Raises :class:`CatalogError` if the response exceeds *max_bytes*.

    The HEAD probe is treated as an optional size hint — some HTTPS
    servers, CDNs, and API gateways answer HEAD with 403/405/501 even
    when GET works.  If HEAD fails or is refused, we skip the pre-check
    and rely on the streaming byte cap below.
    """
    from cvcpkg.storage import get_backend

    try:
        backend = get_backend(url)
        try:
            info = backend.head(url)
        except Exception:
            # HEAD refused / unsupported — fall through to GET and let
            # the streaming cap enforce the size limit.
            info = None
        if info is not None and info.size >= 0 and info.size > max_bytes:
            raise CatalogError(f"catalog at {url} is {info.size} bytes, exceeds {max_bytes} limit")
        chunks: list[bytes] = []
        total = 0
        with backend.open(url) as stream:
            while True:
                chunk = stream.read(1 << 16)  # 64 KB
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise CatalogError(f"catalog at {url} exceeds {max_bytes} byte limit")
                chunks.append(chunk)
        return b"".join(chunks)
    except CatalogError:
        raise
    except Exception as e:
        raise CatalogError(f"failed to fetch {url}: {e}") from e


def fetch_catalog(
    url: str = "",
    *,
    cache_dir: Path | None = None,
    expected_sha256: str = "",
    fallback_urls: list[str] | None = None,
) -> dict:
    """Fetch a catalog YAML and return it as a dict.

    Tries *url* first, then each URL in *fallback_urls* in order.
    If *cache_dir* is given, cache the raw bytes under
    ``<cache_dir>/catalog/<sha256>.yaml``.
    """
    url = url or default_catalog_url()

    urls_to_try = [url] + (fallback_urls or [])
    last_error: Exception | None = None

    for try_url in urls_to_try:
        try:
            data = _fetch_url(try_url)
        except CatalogError as e:
            last_error = e
            if len(urls_to_try) > 1:
                import sys

                print(f"cvcpkg: catalog fetch failed for {try_url}: {e}", file=sys.stderr)
                print("cvcpkg: trying next catalog source...", file=sys.stderr)
            continue

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

    raise CatalogError(f"all catalog sources failed; last error: {last_error}")


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
                mirror_urls=b.get("mirror_urls", []),
                signature=b.get("signature", ""),
                key_fingerprint=b.get("key_fingerprint", ""),
                org=b.get("org", ""),
            )
        )
    return entries


# ── Catalog generation ──────────────────────────────────────────


def generate_catalog(
    indexes_dir: Path,
    output_dir: Path,
    *,
    release_tag: str,
    server_url: str = "",
    base_revision: int = 0,
) -> dict:
    """Merge per-platform index YAMLs into a unified catalog.

    Reads ``*-index.yaml`` files from *indexes_dir*, enriches each
    bundle with ``source_release`` and ``archive_url``, then writes:

    - ``latest.yaml`` — stable entry point
    - ``<revision>.yaml`` — immutable snapshot
    - ``index.yaml`` — revision manifest
    - ``<release_tag>-index.yaml`` — release-specific index

    Returns the catalog dict.
    """
    index_files = sorted(indexes_dir.glob("*-index.yaml"))
    version = release_tag.lstrip("v")

    if not server_url:
        from cvcpkg.config import default_server_url

        server_url = default_server_url()

    all_bundles: list[dict] = []
    for idx_path in index_files:
        idx = yaml.safe_load(idx_path.read_text())
        if not isinstance(idx, dict):
            continue
        for bundle in idx.get("bundles", []):
            bundle["source_release"] = release_tag
            if "archive" in bundle:
                if not bundle.get("archive_url"):
                    bundle["archive_url"] = f"{server_url}/v1/download/{bundle['archive']}"
                bundle.pop("mirror_urls", None)
            all_bundles.append(bundle)

    revision = base_revision + 1
    catalog = {
        "schema_version": 1,
        "revision": revision,
        "bundles": all_bundles,
    }

    catalog_yaml = yaml.dump(catalog, default_flow_style=False, sort_keys=False)
    catalog_sha = hashlib.sha256(catalog_yaml.encode()).hexdigest()

    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "latest.yaml").write_text(catalog_yaml)
    (output_dir / f"{revision}.yaml").write_text(catalog_yaml)

    index_manifest = {
        "latest_revision": revision,
        "revisions": [
            {"revision": revision, "sha256": catalog_sha, "release": release_tag},
        ],
    }
    (output_dir / "index.yaml").write_text(
        yaml.dump(index_manifest, default_flow_style=False, sort_keys=False)
    )

    release_index = {
        "schema_version": 1,
        "release_version": version,
        "recommended": {b["name"]: b["version"] for b in all_bundles},
        "bundles": all_bundles,
    }
    (output_dir / f"{release_tag}-index.yaml").write_text(
        yaml.dump(release_index, default_flow_style=False, sort_keys=False)
    )

    return catalog
