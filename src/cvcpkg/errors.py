# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Typed exception hierarchy for cvcpkg."""

from __future__ import annotations


class CvcpkgError(Exception):
    """Base exception for all cvcpkg errors."""


class SchemaError(CvcpkgError):
    """A YAML document does not conform to its schema."""


class CatalogError(CvcpkgError):
    """Problem fetching or parsing the catalog."""


class ResolveError(CvcpkgError):
    """The resolver could not find a consistent set of bundles."""


class IntegrityError(CvcpkgError):
    """SHA-256 mismatch on a downloaded archive or installed file."""


class DownloadIntegrityError(IntegrityError):
    """A downloaded archive whose bytes do not hash to the catalog's sha256.

    Carries what the transfer looked like (HTTP status, Content-Length, bytes
    received, how many times it was fetched), so the error that reaches a CI
    log can tell a cut-off body from a wrong one without anyone re-running it.
    """

    def __init__(
        self,
        filename: str,
        expected: str,
        actual: str,
        *,
        url: str = "",
        status: int | None = None,
        content_length: int | None = None,
        received: int = 0,
        attempts: int = 1,
        actuals: tuple[str, ...] = (),
    ) -> None:
        self.filename = filename
        self.expected = expected
        self.actual = actual
        self.url = url
        self.status = status
        self.content_length = content_length
        self.received = received
        self.attempts = attempts
        # Every wrong sha256 seen, one per attempt: the same value each time
        # means the server consistently serves other bytes, different values
        # mean the transfer itself is mangling them.
        self.actuals = actuals or (actual,)
        super().__init__(self._message())

    def _message(self) -> str:
        # Keep the historical prefix verbatim: it is what people grep CI for.
        msg = f"sha256 mismatch for {self.filename}: expected {self.expected}, got {self.actual}"
        transfer = [
            f"HTTP {self.status}" if self.status is not None else "",
            (
                f"Content-Length {self.content_length}"
                if self.content_length is not None
                else "no Content-Length"
            ),
            f"received {self.received} bytes",
        ]
        detail = ", ".join(t for t in transfer if t)
        if self.url:
            detail += f" from {self.url}"
        if self.attempts > 1:
            distinct = len(set(self.actuals))
            seen = (
                "the same wrong sha256 each time"
                if distinct == 1
                else f"{distinct} different wrong sha256s"
            )
            detail = f"{self.attempts} attempts, each a fresh download; {seen}; last: {detail}"
        return f"{msg} ({detail})"


class InstallError(CvcpkgError):
    """A bundle could not be installed into the prefix."""


class CollisionError(InstallError):
    """Two bundles ship differing files at the same path."""


class ConflictError(InstallError):
    """A package to be installed conflicts with an already-installed (or
    co-requested) package.  The error message names both packages and
    tells the user which one to uninstall first."""


class AbiError(CvcpkgError):
    """ABI-tag mismatch between two bundles."""


class SigningError(CvcpkgError):
    """A signing or verification operation failed."""
