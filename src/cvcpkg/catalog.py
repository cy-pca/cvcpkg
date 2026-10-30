# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Fetch, parse, and generate the libcvc-deps bundle catalog."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from cvcpkg.config import default_catalog_url
from cvcpkg.errors import CatalogError, IntegrityError
from cvcpkg.manifest import CatalogEntry, Dependency
from cvcpkg.retry import with_retry
from cvcpkg.revisions import revision_of

GITHUB_CATALOG_URL = "https://transfix.github.io/libcvc-deps/catalog/latest.yaml"


_MAX_CATALOG_BYTES = 50 * 1024 * 1024  # 50 MB safety limit


@dataclass(frozen=True)
class _Fetched:
    """One catalog response.  A 304 has no body: the caller's copy is current."""

    status: int
    body: bytes
    etag: str = ""
    content_type: str = ""


def _gunzip(data: bytes, url: str, max_bytes: int) -> bytes:
    """Decompress a gzip body, holding the result to *max_bytes*."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(data, max_bytes + 1)
    except zlib.error as e:
        raise CatalogError(f"catalog at {url} is not valid gzip: {e}") from e
    if len(out) > max_bytes or d.unconsumed_tail:
        raise CatalogError(f"catalog at {url} exceeds {max_bytes} byte limit")
    if not d.eof:
        # A gzip stream cut short is a transport fault, like a body short of its
        # Content-Length, so it is retried rather than parsed.
        raise http.client.IncompleteRead(out)
    return out


def _fetch_url(
    url: str, *, max_bytes: int = _MAX_CATALOG_BYTES, if_none_match: str = ""
) -> _Fetched:
    """Fetch a catalog URL.

    Raises :class:`CatalogError` if the response exceeds *max_bytes*.

    The HEAD probe is treated as an optional size hint — some HTTPS
    servers, CDNs, and API gateways answer HEAD with 403/405/501 even
    when GET works.  If HEAD fails or is refused, we skip the pre-check
    and rely on the streaming byte cap below.

    Over HTTP(S) the request asks for gzip (the registry's catalog is ~7 MB
    of JSON and ~0.5 MB compressed) and, given *if_none_match*, revalidates:
    the result's status is then 304 when that ETag is still current.
    """
    from cvcpkg.config import authorize_request
    from cvcpkg.storage import get_backend

    # Authenticate ONLY to our own server (origin-scoped): a private org's
    # catalog is served to an authenticated member.  For the public GitHub
    # Pages fallback and any other host, this leaves the URL unchanged and sends
    # no header, so the token never leaves our origin.
    url, auth = authorize_request(url)
    headers = dict(auth)
    if url.startswith(("https://", "http://")):
        headers["Accept-Encoding"] = "gzip"
        if if_none_match:
            headers["If-None-Match"] = if_none_match

    try:
        backend = get_backend(url)
        try:
            info = backend.head(url, headers=auth) if auth else backend.head(url)
        except Exception:
            # HEAD refused / unsupported — fall through to GET and let
            # the streaming cap enforce the size limit.
            info = None
        if info is not None and info.size >= 0 and info.size > max_bytes:
            raise CatalogError(f"catalog at {url} is {info.size} bytes, exceeds {max_bytes} limit")
        chunks: list[bytes] = []
        total = 0
        with backend.open(url, headers=headers) if headers else backend.open(url) as stream:
            status = getattr(stream, "status", None) or 200
            resp_headers = getattr(stream, "headers", None) or {}
            etag = resp_headers.get("ETag") or ""
            if status == 304:
                return _Fetched(304, b"", etag or if_none_match)
            while True:
                chunk = stream.read(1 << 16)  # 64 KB
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise CatalogError(f"catalog at {url} exceeds {max_bytes} byte limit")
                chunks.append(chunk)
        body = b"".join(chunks)
        if (resp_headers.get("Content-Encoding") or "").strip().lower() == "gzip":
            body = _gunzip(body, url, max_bytes)
        return _Fetched(200, body, etag, resp_headers.get("Content-Type") or "")
    except CatalogError:
        raise
    except Exception as e:
        raise CatalogError(f"failed to fetch {url}: {e}") from e


def _parse_catalog(data: bytes, content_type: str = "") -> dict:
    """Parse a catalog body: JSON from the registry, YAML from static hosts.

    The registry's /v1/catalog is ~7 MB of JSON.  ``yaml.safe_load`` reads
    JSON too, but took 19 s on it where ``json.loads`` takes 60 ms.
    """
    if "json" in content_type.lower() or data.lstrip()[:1] == b"{":
        try:
            return json.loads(data)
        except ValueError:
            pass  # a YAML flow mapping also starts with "{"
    return yaml.safe_load(data)


class _CatalogCache:
    """The last catalog fetched from each URL, by credential, with its ETag.

    Kept flat in ``<cache_dir>/catalog/`` as ``<key>.body`` and
    ``<key>.json``.  The key hashes the URL with the Authorization header
    the request carries, so a body fetched with one token (one set of
    private orgs) is never revalidated or reused with another.
    """

    def __init__(self, cache_dir: Path, url: str) -> None:
        from cvcpkg.config import authorize_request

        url, auth = authorize_request(url)
        key = hashlib.sha256(f"{url}\0{auth.get('Authorization', '')}".encode()).hexdigest()
        self._dir = cache_dir / "catalog"
        self._body = self._dir / f"{key[:32]}.body"
        self._meta = self._dir / f"{key[:32]}.json"

    def load(self) -> tuple[str, bytes, str] | None:
        """(etag, body, content_type) when an intact copy with an ETag is cached."""
        try:
            meta = json.loads(self._meta.read_text())
            body = self._body.read_bytes()
        except (OSError, ValueError):
            return None
        etag = meta.get("etag") or ""
        if not etag or hashlib.sha256(body).hexdigest() != meta.get("sha256"):
            return None
        return etag, body, meta.get("content_type") or ""

    def store(self, etag: str, body: bytes, content_type: str) -> None:
        meta = {
            "etag": etag,
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": content_type,
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            # Write-then-rename, body first: a concurrent cvcpkg either sees the
            # old pair or one whose sha256 check fails and refetches.
            for path, data in ((self._body, body), (self._meta, json.dumps(meta).encode())):
                tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
        except OSError:
            pass  # a read-only or full cache only costs the next full download


def fetch_catalog(
    url: str = "",
    *,
    cache_dir: Path | None = None,
    expected_sha256: str = "",
    fallback_urls: list[str] | None = None,
) -> dict:
    """Fetch a catalog (JSON or YAML) and return it as a dict.

    Tries *url* first, then each URL in *fallback_urls* in order.
    If *cache_dir* is given, the body is kept under ``<cache_dir>/catalog/``
    with its ETag, and the next fetch of the same URL with the same
    credentials sends ``If-None-Match``: a 304 reuses the kept copy instead
    of downloading the catalog again.
    """
    url = url or default_catalog_url()

    urls_to_try = [url] + (fallback_urls or [])
    last_error: Exception | None = None

    for try_url in urls_to_try:
        cache = _CatalogCache(cache_dir, try_url) if cache_dir else None
        cached = cache.load() if cache else None
        try:
            # Retry the whole body, not only connection setup: a catalog cut
            # off mid-transfer (the server restarting under a deploy) raises a
            # transient TruncatedBody from the https backend.
            fetched = with_retry(
                lambda u=try_url, tag=(cached[0] if cached else ""): _fetch_url(
                    u, if_none_match=tag
                ),
                what=f"fetch catalog {try_url}",
            )
            if fetched.status == 304 and not cached:
                raise CatalogError(f"{try_url} answered 304 to a request with no ETag")
        except CatalogError as e:
            last_error = e
            if len(urls_to_try) > 1:
                import sys

                print(f"cvcpkg: catalog fetch failed for {try_url}: {e}", file=sys.stderr)
                print("cvcpkg: trying next catalog source...", file=sys.stderr)
            continue

        if fetched.status == 304:
            _, data, content_type = cached
        else:
            data, content_type = fetched.body, fetched.content_type

        actual_sha = hashlib.sha256(data).hexdigest()

        if expected_sha256 and actual_sha != expected_sha256:
            raise IntegrityError(
                f"catalog sha256 mismatch: expected {expected_sha256}, got {actual_sha}"
            )

        if cache and fetched.status != 304 and fetched.etag:
            cache.store(fetched.etag, data, content_type)

        return _parse_catalog(data, content_type)

    raise CatalogError(f"all catalog sources failed; last error: {last_error}")


def load_catalog_from_file(path: str | Path) -> dict:
    """Load a catalog YAML from a local file."""
    with open(path) as f:
        return yaml.safe_load(f)


def fetch_authoritative_catalog(
    *,
    server_catalog_url: str = "",
    root_catalog_url: str = "",
    cache_dir: Path | None = None,
    fallback_urls: list[str] | None = None,
) -> dict:
    """Fetch a catalog with the **root authoritative for public packages**.

    Top-down resolution (roadmap Phase 12): the root server is authoritative
    for the public namespace; the local/satellite server supplies organization
    packages and serves as the offline fallback.

    - When *root_catalog_url* is empty or equals *server_catalog_url*, this is
      a plain :func:`fetch_catalog` of the server catalog (no distinct root).
    - Otherwise it fetches the **root** catalog (authoritative public) and the
      **local** catalog (org packages), and merges them root-authoritative.
      If the **root is unreachable**, it falls back to the local catalog so an
      offline / air-gapped satellite still resolves.
    """
    from cvcpkg.config import default_catalog_url, default_root_catalog_url
    from cvcpkg.root_resolution import merge_root_authoritative

    server_catalog_url = server_catalog_url or default_catalog_url()
    root_catalog_url = root_catalog_url or default_root_catalog_url()

    if not root_catalog_url or root_catalog_url == server_catalog_url:
        return fetch_catalog(server_catalog_url, cache_dir=cache_dir, fallback_urls=fallback_urls)

    # Local catalog (org packages + offline public fallback).
    try:
        local_cat = fetch_catalog(server_catalog_url, cache_dir=cache_dir)
    except CatalogError:
        local_cat = None

    # Authoritative root catalog for public packages.
    try:
        root_cat = fetch_catalog(root_catalog_url, cache_dir=cache_dir, fallback_urls=fallback_urls)
    except CatalogError:
        if local_cat is None:
            raise
        import sys

        print(
            f"cvcpkg: root {root_catalog_url} unreachable; resolving from the "
            f"local mirror (offline).",
            file=sys.stderr,
        )
        root_cat = None

    return merge_root_authoritative(root_cat, local_cat)


def trust_mirror_default() -> bool:
    """Whether to accept a mirror's ruling over its upstream's.

    False -- upstream wins.  A mirror may serve a bundle its upstream retired,
    because the mirror operator unyanked it locally; taking that at face value
    would silently reinstate a bundle that was withdrawn for being broken, or
    for a CVE, on every machine pointed at that mirror.  Opting in is explicit:
    ``--trust-mirror``, or CVCPKG_TRUST_MIRROR=1 for non-interactive use.

    CVCPKG_TRUST_MIRROR is input-only -- read here, never written.  A command
    overriding it passes ``trust_mirror=`` to :func:`catalog_entries` instead,
    so the override is scoped to that resolution.  Exporting it would make one
    command's opt-in the standing policy for every later resolution in a
    long-lived or embedding process -- silently reinstating withdrawn bundles,
    which is the exact failure this default exists to prevent.
    """
    import os

    return os.environ.get("CVCPKG_TRUST_MIRROR", "").strip().lower() in ("1", "true", "yes")


def catalog_entries(
    catalog: dict,
    *,
    platform: str = "",
    arch: str = "",
    build_type: str = "",
    link: str = "",
    trust_mirror: bool | None = None,
) -> list[CatalogEntry]:
    """Extract CatalogEntry objects from a catalog dict, optionally filtered."""
    from cvcpkg.platform import arch_matches, is_noarch_bundle, platform_matches

    _trust = trust_mirror_default() if trust_mirror is None else trust_mirror
    entries: list[CatalogEntry] = []
    for b in catalog.get("bundles", []):
        # A noarch bundle (platform=any/arch=noarch) is valid on every host, so
        # it must match a concrete platform/arch request -- otherwise it would
        # publish but never resolve.
        if platform and not platform_matches(b.get("platform", ""), platform):
            continue
        if arch and not arch_matches(b.get("arch", ""), arch):
            continue
        # ...and it compiles nothing, so it serves every config and link mode
        # too.  A noarch recipe publishes one release/shared bundle, and a
        # static request (wasm/wasm-mt/wasi/cosmo are static-only) or a debug
        # one never matched it: no recipe for those could depend on a font, a
        # data set or a JS library.  _prefer_exact_noarch_variant keeps the
        # exact variant when a noarch package publishes more than one.
        noarch = is_noarch_bundle(b.get("platform", ""), b.get("arch", ""))
        if build_type and b.get("build_type", "") != build_type and not noarch:
            continue
        if link and b.get("link", "") != link and not noarch:
            continue
        # Upstream retired this bundle; the mirror serving it has overridden
        # that locally.  Upstream is authoritative unless told otherwise.
        if b.get("upstream_yanked") and not _trust:
            continue
        entries.append(
            CatalogEntry(
                name=b["name"],
                version=b["version"],
                upstream_version=b.get("upstream_version", ""),
                # Prefer an explicit field, else recover the revision from the
                # version string.  /v1/catalog ships "0.3.28+cvc.5" and NO
                # cvc_revision field, so a bare .get() defaulted EVERY entry to
                # 1: revision selection then worked only because
                # version_sort_key independently re-parses the string.  A
                # producer that omits the suffix (the server catalog is
                # documented as doing exactly that) would tie every revision
                # and hand back an arbitrary one — the "install picked a stale
                # revision" symptom.  Derive it so both inputs agree.
                cvc_revision=b.get("cvc_revision") or revision_of(b.get("version", "")) or 1,
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
                upstream_yanked=bool(b.get("upstream_yanked", False)),
                org=b.get("org", ""),
                provides=b.get("provides", []),
                requires_capabilities=b.get("requires_capabilities", []),
            )
        )
    return _prefer_exact_noarch_variant(entries, build_type=build_type, link=link)


def _prefer_exact_noarch_variant(
    entries: list[CatalogEntry], *, build_type: str, link: str
) -> list[CatalogEntry]:
    """Of the noarch bundles of one (org, name, version), keep the nearest variant.

    catalog_entries lets a noarch bundle through whatever config and link were
    asked for.  When a noarch package published more than one variant of the
    same version (release/shared and release/static, say), keep only those
    matching the request best -- both config and link, else one of them -- so
    the resolver never picks a mismatched one by catalog order.  Concrete
    bundles are untouched.
    """
    from cvcpkg.platform import is_noarch_bundle

    if not build_type and not link:
        return entries

    def score(e: CatalogEntry) -> int:
        return int(not build_type or e.build_type == build_type) + int(not link or e.link == link)

    best: dict[tuple[str, str, str], int] = {}
    for e in entries:
        if is_noarch_bundle(e.platform, e.arch):
            key = (e.org, e.name, e.version)
            best[key] = max(best.get(key, 0), score(e))
    return [
        e
        for e in entries
        if not is_noarch_bundle(e.platform, e.arch) or score(e) == best[(e.org, e.name, e.version)]
    ]


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

    # Recommend the newest version per component by the canonical ordering key
    # rather than last-write-wins over the bundle list (which let input order,
    # not version, decide the baseline).
    from cvcpkg.semver import version_sort_key

    recommended: dict[str, str] = {}
    for b in all_bundles:
        bname = b["name"]
        bver = b["version"]
        cur = recommended.get(bname)
        if cur is None or version_sort_key(bver) > version_sort_key(cur):
            recommended[bname] = bver

    release_index = {
        "schema_version": 1,
        "release_version": version,
        "recommended": recommended,
        "bundles": all_bundles,
    }
    (output_dir / f"{release_tag}-index.yaml").write_text(
        yaml.dump(release_index, default_flow_style=False, sort_keys=False)
    )

    return catalog


def newer_revision_available(
    entries: list[CatalogEntry],
    picked: CatalogEntry,
) -> CatalogEntry | None:
    """Return an entry for the SAME variant that outranks *picked*, if any.

    A safety net for a report we could not reproduce: `cvcpkg install openblas`
    returned +cvc.3 while +cvc.4 and +cvc.5 were published.  Selection itself
    tested correct, so the likely cause was a stale client-side catalog rather
    than bad ordering — but "likely" is not "known", and the evidence was
    destroyed before it could be confirmed.

    Rather than guess at the cause, make a recurrence self-diagnosing: compare
    what was picked against every candidate for the identical coordinates
    (name/platform/arch/build_type/link/org) and report anything strictly
    newer.  Under correct ordering and an unconstrained request this returns
    None, so a hit means EITHER a genuine selection bug OR a version
    constraint pinning an older revision — the caller knows which of those it
    asked for and phrases the message accordingly.

    Deliberately compares with the same key the resolver sorts by, so this can
    never disagree with selection about what "newer" means.
    """
    from cvcpkg.semver import version_sort_key

    def _coords(e: CatalogEntry) -> tuple:
        return (
            e.name,
            getattr(e, "org", "") or "",
            e.platform,
            e.arch,
            e.build_type,
            e.link,
        )

    picked_key = version_sort_key(picked.version, picked.cvc_revision)
    best: CatalogEntry | None = None
    best_key = picked_key
    for e in entries:
        if _coords(e) != _coords(picked):
            continue
        k = version_sort_key(e.version, e.cvc_revision)
        if k > best_key:
            best, best_key = e, k
    return best
