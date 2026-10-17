# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""HTTPS / HTTP storage backend (stdlib ``urllib``)."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from typing import Any, BinaryIO, ClassVar

from cvcpkg.retry import with_retry
from cvcpkg.storage import ObjectInfo, StorageBackend


class TruncatedBody(http.client.IncompleteRead):
    """The connection closed before the body reached its Content-Length.

    ``http.client`` does not raise for this: ``HTTPResponse.read(n)`` returns
    ``b""`` at a premature EOF (CPython keeps that for compatibility), and the
    ssl module's ragged-EOF suppression does the same over https.  So a server
    that went away mid-transfer handed callers a short body that looked
    complete.  On 2026-10-12 a production deploy did exactly that: the client
    hashed the first 65,536 bytes of python311's 81 MB archive (and the first
    620 x 64 KiB of emsdk's) and reported a sha256 mismatch, which is never
    retried.  As an ``IncompleteRead`` it is what it really is, a transport
    fault, and ``cvcpkg.retry`` asks again.
    """

    def __init__(self, received: int, content_length: int) -> None:
        super().__init__(b"", content_length - received)
        self.received = received
        self.content_length = content_length

    def __repr__(self) -> str:
        return (
            f"connection closed after {self.received} of {self.content_length} bytes "
            "(Content-Length)"
        )

    __str__ = __repr__


def _content_length(headers: Any) -> int | None:
    try:
        value = headers.get("Content-Length") if headers is not None else None
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _CheckedBody:
    """A response body that cannot end before its Content-Length.

    Wraps the ``HTTPResponse`` that :meth:`HttpsBackend.open` returns and
    exposes ``status``, ``headers``, ``content_length`` and ``received`` so a
    caller can report what the transfer looked like.
    """

    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.status: int | None = getattr(resp, "status", None)
        self.headers = getattr(resp, "headers", None)
        self.content_length = _content_length(self.headers)
        self.received = 0

    def read(self, amt: int | None = -1) -> bytes:
        unbounded = amt is None or amt < 0
        data = self._resp.read() if unbounded else self._resp.read(amt)
        self.received += len(data)
        ended = unbounded or (amt and not data)
        if ended and self.content_length is not None and self.received < self.content_length:
            raise TruncatedBody(self.received, self.content_length)
        return data

    def close(self) -> None:
        close = getattr(self._resp, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> _CheckedBody:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    def __getattr__(self, name: str) -> Any:
        if name == "_resp":  # not yet set (copy/pickle): do not recurse
            raise AttributeError(name)
        return getattr(self._resp, name)


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
        # path's job (see installer._download_from_url).  The body is wrapped
        # so that a connection cut short of Content-Length raises TruncatedBody
        # instead of reading as a normal EOF.
        hdrs = self._headers(headers)

        def _once() -> BinaryIO:
            req = urllib.request.Request(uri, headers=hdrs)
            try:
                resp = urllib.request.urlopen(req, timeout=120)  # noqa: S310
            except urllib.error.HTTPError as exc:
                # A 304 answers a conditional request (If-None-Match): the
                # caller's copy is current.  urllib raises it like an error;
                # hand it back instead, with its status and headers, and no
                # body to read.  Only a caller that sent a validator gets one.
                if exc.code != 304:
                    raise
                resp = exc
            return _CheckedBody(resp)  # type: ignore[return-value]

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
