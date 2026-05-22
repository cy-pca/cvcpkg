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
    """Convert a ``file://`` URI or plain path to a ``Path``."""
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"FileBackend does not handle scheme '{parsed.scheme}'")
    # file:///absolute/path or file://host/path or plain /path
    path_str = unquote(parsed.path)
    if not path_str:
        path_str = unquote(parsed.netloc + parsed.path) if parsed.netloc else uri
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
