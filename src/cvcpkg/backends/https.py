"""HTTPS / HTTP storage backend (stdlib ``urllib``)."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import BinaryIO, ClassVar

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

    def head(self, uri: str) -> ObjectInfo:
        req = urllib.request.Request(uri, method="HEAD", headers={"User-Agent": _user_agent()})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                size = int(resp.headers.get("Content-Length", -1))
                etag = resp.headers.get("ETag", "")
                ct = resp.headers.get("Content-Type", "")
                return ObjectInfo(size=size, etag=etag, content_type=ct)
        except urllib.error.URLError as exc:
            raise OSError(f"HEAD {uri}: {exc}") from exc

    def open(self, uri: str) -> BinaryIO:
        req = urllib.request.Request(uri, headers={"User-Agent": _user_agent()})
        try:
            resp = urllib.request.urlopen(req, timeout=120)  # noqa: S310
            return resp  # type: ignore[return-value]
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
