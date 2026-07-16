"""Backend-aware archive storage for the cvcpkg server.

The server keeps package archives in a storage backend selected by the
server's ``storage_uri`` (default ``file://<state-dir>``).  For local
(``file://``) backends archives live on disk and are read/written directly;
for remote backends (``s3://`` — e.g. a Garage cluster — and the other
``cvcpkg.storage`` backends) they are streamed through
:func:`cvcpkg.storage.get_backend`.

Every archive filename maps to a deterministic URI *under* the storage root::

    <storage_uri>/archives/<filename>

This module is the single place that composes that URI and performs the
read/write/exist/size/checksum primitives, so publish, download, migration
and the storage doctor all agree on where an archive lives and how its
integrity is checked.

The active ``storage_uri`` is persisted to ``<state-dir>/storage.yaml`` so a
migration survives a server restart (the server reads it back on startup when
no explicit ``--storage`` is given).  Credentials/endpoints for remote
backends are NOT persisted here — they come from the environment
(``CVCPKG_S3_ENDPOINT_URL``, ``AWS_ACCESS_KEY_ID`` …) at call time.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

import yaml

from cvcpkg.storage import get_backend

# Kept in sync with app.py's _ARCHIVES_DIR: the subdirectory/prefix under the
# storage root where archives live.
ARCHIVES_SUBDIR = "archives"
STORAGE_CONFIG_FILE = "storage.yaml"

_READ_CHUNK = 1 << 16  # 64 KiB


# ── URI / path composition ──────────────────────────────────────


def scheme_of(uri: str) -> str:
    """Return the lower-cased URI scheme, defaulting to ``file``."""
    return (urlparse(uri).scheme or "file").lower()


def is_local(storage_uri: str) -> bool:
    """Whether *storage_uri* names the local-filesystem backend."""
    return scheme_of(storage_uri) == "file"


def archive_uri(storage_uri: str, filename: str) -> str:
    """Full backend URI for *filename* under *storage_uri*."""
    return f"{storage_uri.rstrip('/')}/{ARCHIVES_SUBDIR}/{filename}"


def local_root(storage_uri: str) -> Path | None:
    """Local archives directory when *storage_uri* is ``file://``, else None."""
    if not is_local(storage_uri):
        return None
    # Reuse the file backend's own URI→path logic (handles Windows drive
    # letters, NFS/SMB mounts, and plain paths passed without a scheme).
    from cvcpkg.backends.local import _uri_to_path

    return _uri_to_path(storage_uri) / ARCHIVES_SUBDIR


def local_path(storage_uri: str, filename: str) -> Path | None:
    """Local on-disk path for *filename*, or None for a remote backend."""
    root = local_root(storage_uri)
    return (root / filename) if root is not None else None


# ── Persisted active storage_uri ────────────────────────────────


def load_storage_uri(state_dir: Path, default: str) -> str:
    """Return the persisted storage_uri for *state_dir*, or *default*."""
    cfg = Path(state_dir) / STORAGE_CONFIG_FILE
    if cfg.is_file():
        try:
            data = yaml.safe_load(cfg.read_text()) or {}
        except Exception:
            data = {}
        uri = data.get("storage_uri")
        if isinstance(uri, str) and uri:
            return uri
    return default


def save_storage_uri(state_dir: Path, storage_uri: str) -> None:
    """Persist *storage_uri* as the active backend for *state_dir*."""
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / STORAGE_CONFIG_FILE).write_text(
        yaml.safe_dump({"storage_uri": storage_uri}, default_flow_style=False)
    )


# ── Read / write / integrity primitives ─────────────────────────


def store(storage_uri: str, filename: str, src: Path) -> None:
    """Move the staged archive at *src* into the backend for *filename*.

    For a local backend this is a filesystem move (atomic when *src* is on the
    same volume, falling back to a copy across volumes).  For a remote backend
    the bytes are uploaded via ``backend.put`` and the local staging file is
    removed.
    """
    src = Path(src)
    lp = local_path(storage_uri, filename)
    if lp is not None:
        lp.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.replace(lp)  # atomic on the same filesystem
        except OSError:
            shutil.move(str(src), str(lp))  # cross-device
        return
    backend = get_backend(storage_uri)
    size = src.stat().st_size
    with open(src, "rb") as f:
        backend.put(archive_uri(storage_uri, filename), f, size)
    src.unlink(missing_ok=True)


def open_stream(storage_uri: str, filename: str) -> BinaryIO:
    """Return a binary reader for the stored archive *filename*."""
    lp = local_path(storage_uri, filename)
    if lp is not None:
        return open(lp, "rb")
    return get_backend(storage_uri).open(archive_uri(storage_uri, filename))


def exists(storage_uri: str, filename: str) -> bool:
    """Whether *filename* is present in the backend for *storage_uri*."""
    lp = local_path(storage_uri, filename)
    if lp is not None:
        return lp.is_file()
    try:
        get_backend(storage_uri).head(archive_uri(storage_uri, filename))
        return True
    except Exception:
        return False


def size(storage_uri: str, filename: str) -> int:
    """Return the byte size of *filename*, or -1 if unknown/absent."""
    lp = local_path(storage_uri, filename)
    if lp is not None:
        try:
            return lp.stat().st_size
        except OSError:
            return -1
    try:
        return int(get_backend(storage_uri).head(archive_uri(storage_uri, filename)).size)
    except Exception:
        return -1


def sha256_of(storage_uri: str, filename: str) -> str:
    """Stream *filename* from the backend and return its SHA-256 hex digest.

    Raises if the archive cannot be read — callers treat that as "missing".
    """
    h = hashlib.sha256()
    with open_stream(storage_uri, filename) as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def copy_archive(src_uri: str, dst_uri: str, filename: str) -> str:
    """Copy *filename* from the *src_uri* backend to the *dst_uri* backend.

    Streams the bytes through and returns the SHA-256 of what was copied so the
    caller can verify integrity against the catalog.  Never keeps the whole
    archive in memory.
    """
    h = hashlib.sha256()
    dst_local = local_path(dst_uri, filename)
    with open_stream(src_uri, filename) as reader:
        if dst_local is not None:
            dst_local.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst_local.with_suffix(dst_local.suffix + ".migrating")
            with open(tmp, "wb") as w:
                while True:
                    chunk = reader.read(_READ_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    w.write(chunk)
            tmp.replace(dst_local)
        else:
            # Remote destination: hash while buffering to a spooled temp, then
            # put in one shot (backends want a single seekable/streamed body).
            import tempfile

            with tempfile.SpooledTemporaryFile(max_size=8 << 20) as spool:
                while True:
                    chunk = reader.read(_READ_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    spool.write(chunk)
                total = spool.tell()
                spool.seek(0)
                get_backend(dst_uri).put(archive_uri(dst_uri, filename), spool, total)
    return h.hexdigest()
