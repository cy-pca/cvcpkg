"""Local filesystem storage backend."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, ClassVar
from urllib.parse import unquote, urlparse

from cvcpkg.storage import ObjectInfo, StorageBackend


def _uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI or plain path to a ``Path``.

    Handles Windows drive-letter URIs robustly across Python versions
    and platforms.  Common cases:

    * ``file:///C:/path``  → netloc='', path='/C:/path'  (correct URI)
    * ``file://C/path``    → netloc='C', path='/path'    (Win backslash normalised)
    * ``file://C:\\path/`` → netloc='C:\\path', path='/' (backslash kept in netloc)
    * ``file://C:\\path``  → netloc='C:\\path', path=''  (no trailing slash)
    * ``file:///tmp/foo``  → netloc='', path='/tmp/foo'  (Unix)
    """
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"FileBackend does not handle scheme '{parsed.scheme}'")
    if not parsed.scheme:
        return Path(uri)

    path_str = unquote(parsed.path)
    netloc = unquote(parsed.netloc)

    # Case: entire Windows path landed in netloc (e.g. C:\Users\...)
    # Detected by drive-letter pattern at position [1].
    # Any remaining path component must be appended (e.g. /file.tar.gz).
    if len(netloc) >= 2 and netloc[1] == ":":
        if path_str and path_str != "/":
            return Path(netloc + path_str)
        return Path(netloc)

    # Case: urlparse normalised backslashes — single-letter netloc is a drive.
    # e.g. file://C/Users/path → netloc='C', path='/Users/path'
    if len(netloc) == 1 and netloc.isalpha():
        return Path(netloc + ":" + path_str)

    # Case: RFC 8089 file URI with the drive letter in the PATH, e.g.
    # file:///C:/Users/x → netloc='', path='/C:/Users/x'.  This is the canonical
    # form Path.as_uri() emits and the docstring's "correct URI" case, but
    # urlparse keeps the leading slash, so a bare Path() yields "\C:\Users\x" on
    # Windows -- a broken path that fails every filesystem op with WinError 123.
    # Strip the leading slash so the drive letter starts the path.
    if len(path_str) >= 3 and path_str[0] == "/" and path_str[1].isalpha() and path_str[2] == ":":
        return Path(path_str[1:])

    # Case: netloc is empty or a network host — use path directly.
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
