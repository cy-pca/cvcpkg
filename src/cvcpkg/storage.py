# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Pluggable storage backends for fetching and pushing bundle archives.

Backends are registered by URI scheme and dispatched automatically.
The protocol is intentionally tiny so new backends are easy to add.

Built-in backends (no extra deps):
  - https, http  — stdlib ``urllib``
  - file          — local filesystem / NFS / SMB
  - gh-release    — GitHub release assets via REST API

Optional extras (install with ``pip install cvcpkg[s3]`` etc.):
  - s3            — AWS S3 / MinIO / any S3-compatible store
  - gs            — Google Cloud Storage
  - azblob        — Azure Blob Storage
  - sftp, ssh     — SFTP via paramiko

Subprocess shims (require the binary on PATH):
  - rsync         — ``rsync`` binary
  - rclone        — ``rclone`` binary
  - s3-cli        — ``aws s3`` CLI fallback

Third-party backends can register via the ``cvcpkg.storage_backends``
entry-point group.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO, ClassVar, Protocol, runtime_checkable
from urllib.parse import urlparse

# ── Object info ─────────────────────────────────────────────────


@dataclass
class ObjectInfo:
    """Metadata returned by :meth:`StorageBackend.head`."""

    size: int = -1
    sha256: str = ""
    etag: str = ""
    content_type: str = ""


# ── Backend protocol ────────────────────────────────────────────


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol that all storage backends must satisfy."""

    schemes: ClassVar[tuple[str, ...]]

    def head(self, uri: str) -> ObjectInfo:
        """Return size and optional pre-computed hashes for *uri*."""
        ...

    def open(self, uri: str) -> BinaryIO:
        """Return a streaming binary reader for *uri*."""
        ...

    def supports_range(self, uri: str) -> bool:
        """Whether the backend supports HTTP-style range requests."""
        ...

    # ── Optional write interface (for ``cvcpkg publish --dest``) ──

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        """Upload *data* to *uri*.  Not all backends support this."""
        raise NotImplementedError(f"{type(self).__name__} is read-only")

    # ── Optional listing (for ``cvcpkg mirror``) ────────────────

    def list(self, uri: str) -> Iterable[str]:
        """List children of *uri* (directory-like listing)."""
        raise NotImplementedError(f"{type(self).__name__} does not support listing")


# ── Backend registry ────────────────────────────────────────────


_registry: dict[str, StorageBackend] = {}
_loaded_builtins = False
_loaded_entrypoints = False


def register(backend: StorageBackend) -> None:
    """Register a backend instance for its declared schemes."""
    for scheme in backend.schemes:
        _registry[scheme.lower()] = backend


def _load_builtins() -> None:
    """Lazy-load built-in backends on first use."""
    global _loaded_builtins
    if _loaded_builtins:
        return
    _loaded_builtins = True

    from cvcpkg.backends.gh_release import GhReleaseBackend
    from cvcpkg.backends.https import HttpsBackend
    from cvcpkg.backends.local import FileBackend
    from cvcpkg.backends.rclone import RcloneBackend
    from cvcpkg.backends.rsync import RsyncBackend

    register(HttpsBackend())
    register(FileBackend())
    register(GhReleaseBackend())
    register(RsyncBackend())
    register(RcloneBackend())


def _load_entrypoints() -> None:
    """Discover third-party backends via entry points."""
    global _loaded_entrypoints
    if _loaded_entrypoints:
        return
    _loaded_entrypoints = True
    try:
        eps = importlib.metadata.entry_points()
        # Python 3.12+ returns a SelectableGroups; 3.9–3.11 returns a dict
        if hasattr(eps, "select"):
            group = eps.select(group="cvcpkg.storage_backends")
        else:
            group = eps.get("cvcpkg.storage_backends", [])
        for ep in group:
            try:
                cls = ep.load()
                register(cls())
            except Exception:
                # Don't crash if a third-party backend fails to load
                pass
    except Exception:
        pass


def _load_optional(scheme: str) -> bool:
    """Try to load an optional backend for *scheme*."""
    optional_map = {
        "s3": "cvcpkg.backends.s3",
        "gs": "cvcpkg.backends.gcs",
        "azblob": "cvcpkg.backends.azure",
        "sftp": "cvcpkg.backends.sftp",
        "ssh": "cvcpkg.backends.sftp",
        "s3-cli": "cvcpkg.backends.s3_cli",
    }
    module_name = optional_map.get(scheme)
    if not module_name:
        return False
    try:
        import importlib

        mod = importlib.import_module(module_name)
        # Expect a module-level BACKEND_CLASS or a class named *Backend
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if isinstance(obj, type) and obj is not StorageBackend and hasattr(obj, "schemes"):
                register(obj())
                return True
    except ImportError:
        return False
    return False


def get_backend(uri: str) -> StorageBackend:
    """Return the backend for *uri*'s scheme.

    Raises ``ValueError`` if no backend is registered.
    """
    _load_builtins()
    _load_entrypoints()

    scheme = urlparse(uri).scheme.lower() or "file"
    if scheme in _registry:
        return _registry[scheme]
    # Try optional backends on demand
    if _load_optional(scheme):
        return _registry[scheme]
    available = sorted(_registry.keys())
    raise ValueError(
        f"No storage backend for scheme '{scheme}' "
        f"(available: {', '.join(available)}). "
        f"Install an optional extra or configure a backend."
    )


def available_schemes() -> list[str]:
    """Return all registered scheme names."""
    _load_builtins()
    _load_entrypoints()
    return sorted(_registry.keys())
