# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""HTTPS / HTTP storage backend (stdlib ``urllib``)."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import BinaryIO, ClassVar

from cvcpkg.retry import with_retry
from cvcpkg.storage import ObjectInfo, StorageBackend


def _user_agent() -> str:
    """Identify the cvcpkg client (and version) to servers.

    The cvcpkg server's download analytics use the ``cvcpkg/x.y.z``
    User-Agent prefix for client-version distribution (Phase 2 roadmap).
    """
    try:
        from cvcpkg import __version__

        return f"cvcpkg/{__version__}"
    except Exception:
        return "cvcpkg/unknown"


class HttpsBackend(StorageBackend):
    """Fetch objects over HTTPS/HTTP using Python's stdlib.

    Honors ``HTTPS_PROXY``, ``HTTP_PROXY``, and ``NO_PROXY``
    environment variables via urllib's default opener.
    """

    schemes: ClassVar[tuple[str, ...]] = ("https", "http")

    @staticmethod
    def _headers(extra: dict[str, str] | None) -> dict[str, str]:
        """Default User-Agent, with *extra* (e.g. an Authorization header for
        our own server) merged on top."""
        hdrs = {"User-Agent": _user_agent()}
        if extra:
            hdrs.update(extra)
        return hdrs

    def head(self, uri: str, headers: dict[str, str] | None = None) -> ObjectInfo:
        hdrs = self._headers(headers)

        def _once() -> ObjectInfo:
            req = urllib.request.Request(uri, method="HEAD", headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                size = int(resp.headers.get("Content-Length", -1))
                etag = resp.headers.get("ETag", "")
                ct = resp.headers.get("Content-Type", "")
                return ObjectInfo(size=size, etag=etag, content_type=ct)

        try:
            return with_retry(_once, what=f"HEAD {uri}")
        except urllib.error.URLError as exc:
            raise OSError(f"HEAD {uri}: {exc}") from exc

    def open(self, uri: str, headers: dict[str, str] | None = None) -> BinaryIO:
        # Retries only ESTABLISHING the stream. A failure part-way through the
        # body cannot be resumed from here -- the caller has already been handed
        # the file object -- so restarting the whole transfer is the download
        # path's job (see installer._download_from_url).
        hdrs = self._headers(headers)

        def _once() -> BinaryIO:
            req = urllib.request.Request(uri, headers=hdrs)
            resp = urllib.request.urlopen(req, timeout=120)  # noqa: S310
            return resp  # type: ignore[return-value]

        try:
            return with_retry(_once, what=f"GET {uri}")
        except urllib.error.URLError as exc:
            raise OSError(f"GET {uri}: {exc}") from exc

    def supports_range(self, uri: str) -> bool:
        try:
            info = self.head(uri)
            # Most HTTPS servers support ranges; we optimistically say yes
            return info.size > 0
        except OSError:
            return False

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        body = data.read()
        req = urllib.request.Request(uri, data=body, method="PUT")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("User-Agent", _user_agent())
        if size >= 0:
            req.add_header("Content-Length", str(size))
        try:
            urllib.request.urlopen(req, timeout=120)  # noqa: S310
        except urllib.error.URLError as exc:
            raise OSError(f"PUT {uri}: {exc}") from exc
