# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Archive helpers: format detection, and safe extraction.

**Format detection.**  A bundle's filename says nothing reliable about its
bytes.  cvcpkg.org stores every published archive as
``<name>-<version>-<platform>-<arch>-<config>-<link>.tar.zst`` whatever the
producer packed -- a gzip tarball on Linux/macOS/BSD, a zip on Windows
(``create_archive``) -- and the download cache keeps the URL's basename.
Anything that opens a bundle therefore goes by magic bytes:
:func:`archive_format` names the format, :func:`open_tar` opens any tar
flavour it reports, and the suffix is only a fallback for content with no
signature (a pre-POSIX tar).

**Safe extraction.**  A malicious (or poisoned upstream) recipe/package bundle
can contain member names like ``../../../.bashrc`` or absolute paths, or
symlinks/hardlinks whose targets point outside the destination. Extracting such
a member with a plain ``extractall`` would write outside the intended directory
— arbitrary file write → code execution on the next shell. Route every
extraction through :func:`safe_tar_extractall`.
"""

from __future__ import annotations

import sys
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cvcpkg.errors import InstallError

# (offset, signature, format), checked in order.  ustar's signature sits in
# the first header block rather than at the start of the file.
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"PK\x03\x04", "zip"),
    (0, b"PK\x05\x06", "zip"),  # an empty zip is just the end-of-directory record
    (0, b"\x1f\x8b", "tar.gz"),
    (0, b"\x28\xb5\x2f\xfd", "tar.zst"),
    (0, b"\xfd7zXZ\x00", "tar.xz"),
    (0, b"BZh", "tar.bz2"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z"),
    (257, b"ustar", "tar"),
)
_SNIFF_BYTES = 262  # reaches ustar's signature at offset 257

# Fallback for content with no signature; longest suffix first.
_SUFFIXES: tuple[tuple[str, str], ...] = (
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
    (".tar.bz2", "tar.bz2"),
    (".tbz2", "tar.bz2"),
    (".tar.xz", "tar.xz"),
    (".txz", "tar.xz"),
    (".tar.zst", "tar.zst"),
    (".tar.zstd", "tar.zst"),
    (".tar", "tar"),
    (".zip", "zip"),
    (".7z", "7z"),
)

_TAR_FORMATS = frozenset({"tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst"})


def archive_format(path) -> str:
    """Name *path*'s archive format from its content, not its filename.

    Returns ``"zip"``, ``"7z"``, ``"tar"``, ``"tar.gz"``, ``"tar.bz2"``,
    ``"tar.xz"`` or ``"tar.zst"``.  Magic bytes win; the filename suffix is
    consulted only when the content carries no recognised signature.  Raises
    :class:`InstallError` when neither identifies a supported format.
    """
    path = Path(path)
    with open(path, "rb") as fh:
        head = fh.read(_SNIFF_BYTES)
    for offset, magic, fmt in _MAGIC:
        if head[offset : offset + len(magic)] == magic:
            return fmt
    name = path.name.lower()
    for suffix, fmt in _SUFFIXES:
        if name.endswith(suffix):
            return fmt
    raise InstallError(f"unsupported archive format: {path.name}")


@contextmanager
def open_tar(path, *, stream: bool = False, fmt: str | None = None) -> Iterator[tarfile.TarFile]:
    """Open a tar archive of any flavour :func:`archive_format` reports.

    *stream* opens it for one forward pass (``r|*``) that never seeks back;
    use it to iterate the members once.  Leave it off when the caller needs
    random access (``getmembers()`` then ``extractall()``).  Pass *fmt* when
    the caller has already detected the format, to skip a second sniff.

    Raises :class:`InstallError` for a zip or 7z, and for a zstd tar on an
    interpreter whose tarfile cannot decompress it (zstd arrived in Python
    3.14), where tarfile itself would only report "invalid header".
    """
    path = Path(path)
    fmt = fmt or archive_format(path)
    if fmt not in _TAR_FORMATS:
        raise InstallError(f"{path.name} is a {fmt} archive, not a tar")
    try:
        tf = tarfile.open(path, mode="r|*" if stream else "r:*")
    except tarfile.ReadError as exc:
        if fmt == "tar.zst" and "zst" not in tarfile.TarFile.OPEN_METH:
            raise InstallError(
                f"{path.name} is a zstd-compressed tar, which this Python "
                f"({sys.version.split()[0]}) cannot read: zstd support in "
                "tarfile arrived in Python 3.14"
            ) from exc
        raise
    with tf:
        yield tf


def _escapes(dest: Path, candidate: Path) -> bool:
    """True if *candidate* is not *dest* itself and not contained within it."""
    candidate = candidate.resolve()
    return candidate != dest and dest not in candidate.parents


def safe_tar_extractall(tar: tarfile.TarFile, dest) -> None:
    """Extract *tar* into *dest*, rejecting any member that would escape *dest*.

    Checks every member's resolved path (and, for links, the resolved link
    target) for containment, then extracts with the hardened ``data`` filter on
    Python 3.12+ (which also strips setuid bits, block devices, etc.).
    """
    dest = Path(dest).resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if _escapes(dest, target):
            raise ValueError(f"unsafe tar member escapes destination: {member.name!r}")
        if member.issym() or member.islnk():
            # tarfile resolves a SYMLINK target relative to the link's own
            # directory but a HARDLINK target relative to the extraction ROOT.
            # Using the wrong base under-counts the '..' needed to escape (a
            # subdir hardlink like 's/hl' -> 'foo/../../victim' would pass a
            # parent-relative check yet escape at extraction).
            if member.islnk():
                link_target = (dest / member.linkname).resolve()
            else:
                link_target = (target.parent / member.linkname).resolve()
            if _escapes(dest, link_target):
                raise ValueError(
                    f"unsafe tar link '{member.name}' -> '{member.linkname}' escapes destination"
                )
    if sys.version_info >= (3, 12):
        tar.extractall(dest, filter="data")
    else:  # pragma: no cover - version-dependent
        tar.extractall(dest)


def tar_has_unsafe_member(tar: tarfile.TarFile) -> str | None:
    """Return the name of the first member that would escape a relative root,
    or ``None`` if the archive is safe. For validating an UPLOADED bundle before
    storing it (defense in depth alongside the extraction guard)."""
    root = Path("/__cvc_root__").resolve()
    for member in tar.getmembers():
        if _escapes(root, (root / member.name).resolve()):
            return member.name
        if member.issym() or member.islnk():
            # Reject a link whose target escapes the root under EITHER base
            # (symlink: the member's own dir; hardlink: the root). The plain
            # startswith(('/','..')) test misses e.g. 'foo/../../victim'.
            member_dir = (root / member.name).resolve().parent
            for base in (root, member_dir):
                if _escapes(root, (base / member.linkname).resolve()):
                    return member.name
    return None
