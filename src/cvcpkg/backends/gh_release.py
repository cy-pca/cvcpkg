# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""GitHub Release asset storage backend.

Resolves ``gh-release://owner/repo/tag/asset`` URIs to the
asset's CDN download URL via the GitHub REST API.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import BinaryIO, ClassVar
from urllib.parse import urlparse

from cvcpkg.storage import ObjectInfo, StorageBackend

_API_BASE = "https://api.github.com"


def _parse_gh_uri(uri: str) -> tuple[str, str, str, str]:
    """Parse ``gh-release://owner/repo/tag/asset`` → (owner, repo, tag, asset)."""
    parsed = urlparse(uri)
    parts = parsed.netloc.split("/") + [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 4:
        raise ValueError(f"gh-release URI must be gh-release://owner/repo/tag/asset, got: {uri}")
    return parts[0], parts[1], parts[2], "/".join(parts[3:])


def _gh_headers() -> dict[str, str]:
    """Return headers for GitHub API requests, with token if available."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _resolve_asset_url(owner: str, repo: str, tag: str, asset_name: str) -> tuple[str, int]:
    """Resolve a GitHub release asset to its download URL and size.

    Returns (browser_download_url, size_bytes).
    """
    api_url = f"{_API_BASE}/repos/{owner}/{repo}/releases/tags/{tag}"
    req = urllib.request.Request(api_url, headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            release = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise OSError(f"GitHub API error for {api_url}: {exc}") from exc

    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            return asset["browser_download_url"], asset["size"]
    raise FileNotFoundError(f"Asset '{asset_name}' not found in release {owner}/{repo}@{tag}")


class GhReleaseBackend(StorageBackend):
    """Fetch assets from GitHub Releases.

    URI format: ``gh-release://owner/repo/tag/asset-filename``

    Honors ``GITHUB_TOKEN`` / ``GH_TOKEN`` for authenticated
    requests (higher rate limits, private repos).
    """

    schemes: ClassVar[tuple[str, ...]] = ("gh-release",)

    def head(self, uri: str) -> ObjectInfo:
        owner, repo, tag, asset = _parse_gh_uri(uri)
        _, size = _resolve_asset_url(owner, repo, tag, asset)
        return ObjectInfo(size=size)

    def open(self, uri: str) -> BinaryIO:
        owner, repo, tag, asset = _parse_gh_uri(uri)
        url, _ = _resolve_asset_url(owner, repo, tag, asset)
        try:
            return urllib.request.urlopen(url, timeout=120)  # type: ignore[return-value]  # noqa: S310
        except urllib.error.URLError as exc:
            raise OSError(f"Failed to download {url}: {exc}") from exc

    def supports_range(self, uri: str) -> bool:
        return True  # GitHub CDN supports range requests
