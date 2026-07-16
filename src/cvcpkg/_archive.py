"""Safe archive extraction — guards against tar-slip / zip-slip.

A malicious (or poisoned upstream) recipe/package bundle can contain member
names like ``../../../.bashrc`` or absolute paths, or symlinks/hardlinks whose
targets point outside the destination. Extracting such a member with a plain
``extractall`` would write outside the intended directory — arbitrary file write
→ code execution on the next shell. Route every extraction through
:func:`safe_tar_extractall`.
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path


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
        if (member.issym() or member.islnk()) and member.linkname.startswith(("/", "..")):
            return member.name
    return None
