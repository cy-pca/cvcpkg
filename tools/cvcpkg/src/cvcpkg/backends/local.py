"""Local filesystem storage backend."""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path
from typing import BinaryIO, ClassVar, Iterable
from urllib.parse import unquote, urlparse

from cvcpkg.storage import ObjectInfo, StorageBackend


def _uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI or plain path to a ``Path``.

    Handles Windows drive-letter URIs such as ``file://C:/path``
    (which ``urlparse`` splits into ``netloc='C'``, ``path='/path'``)
    and the correct ``file:///C:/path`` form.  Also handles the case
    where the entire Windows path lands in ``netloc`` (e.g. Python
    on Linux parsing ``file://C:\\Users\\path``).
    """
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"FileBackend does not handle scheme '{parsed.scheme}'")
    if not parsed.scheme:
        return Path(uri)

    path_str = unquote(parsed.path)
    netloc = unquote(parsed.netloc)

    # On Windows Python, file://C:\path is parsed as netloc='C', path='/path'.
    # Reconstruct the drive-letter prefix.
    if len(netloc) == 1 and netloc.isalpha():
        return Path(netloc + ":" + path_str)

    # On Linux Python, file://C:\path is parsed with the entire path in netloc
    # (backslash is not a separator). Fall back to netloc + path.
    if netloc and not path_str:
        return Path(netloc)

    if not path_str and netloc:
        return Path(netloc)

    return Path(path_str)


class FileBackend(StorageBackend):
    """Read and write objects on the local filesystem.

    Also works with NFS / SMB mounts.  Accepts ``file://`` URIs
    or absolute paths.
    """

    schemes: ClassVar[tuple[str, ...]] = ("file",)

    def head(self, uri: str) -> ObjectInfo:
        p = _uri_to_path(uri)
        if not p.is_file():
            raise FileNotFoundError(f"not found: {p}")
        return ObjectInfo(size=p.stat().st_size)

    def open(self, uri: str) -> BinaryIO:
        p = _uri_to_path(uri)
        return open(p, "rb")  # noqa: SIM115

    def supports_range(self, uri: str) -> bool:
        return True  # seek() works on local files

    def put(self, uri: str, data: BinaryIO, size: int = -1) -> None:
        p = _uri_to_path(uri)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            shutil.copyfileobj(data, f)

    def list(self, uri: str) -> Iterable[str]:
        p = _uri_to_path(uri)
        if not p.is_dir():
            return []
        return sorted(child.name for child in p.iterdir())
