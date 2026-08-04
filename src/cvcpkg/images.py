# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Installed VM/disk-image discovery — the ``share/<package>/`` image layout.

An *image package* ships a bootable guest disk (plus the metadata a hypervisor
needs to import it) instead of headers and libraries.  Historically the first
such recipe (``haiku-image``) staged its payload at the ROOT of the install
prefix, which put generically-named files — ``metadata.yaml``,
``README-import.md`` — directly in ``$PREFIX``.  Those names are not specific to
any one guest, so a second image package collides with the first on both.

The layout this module defines and reads instead is::

    <prefix>/share/<package-name>/image.yaml          canonical descriptor
    <prefix>/share/<package-name>/image.env           POSIX KEY=value shim
    <prefix>/share/<package-name>/disk.qcow2          the payload
    <prefix>/share/<package-name>/SHA256SUMS          `sha256sum -c` format
    <prefix>/share/<package-name>/README.md           docs
    <prefix>/share/<package-name>/incus/metadata.*    importer metadata

Two properties make it work:

* the directory is the **package name**, which is globally unique in the
  catalog keyspace, so N image packages can never collide; and
* the filenames are **role-based** (``disk.qcow2``, not
  ``haiku-builder-x86_64.qcow2``), so a shell consumer can derive the path from
  the package name alone — no version, no guest arch, no upstream naming
  knowledge.

Guest axes (OS, arch, release, variant) live in the package NAME and inside
``image.yaml``; they never appear in a filename.

Everything here is pure filesystem inspection: discovery is a *glob* over
``<prefix>/share/*/image.yaml``.  There is no index, no server call and no new
state file — which is the point of fixing the layout.
"""

from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cvcpkg.errors import CvcpkgError

# ── Layout constants ────────────────────────────────────────────
#
# These names are the contract.  A consumer with no cvcpkg on PATH is expected
# to hardcode "<prefix>/share/<package>/disk.qcow2"; that is a supported,
# documented fallback, so these strings may not change without a major
# schema_version bump in every image recipe.

SHARE_DIR = "share"
IMAGE_DESCRIPTOR = "image.yaml"
IMAGE_ENV_FILE = "image.env"
CHECKSUM_FILE = "SHA256SUMS"

#: ``image.yaml`` schema revision this module understands.
IMAGE_SCHEMA_VERSION = 1

#: Roles addressable via ``cvcpkg image path --role``.
ROLES = (
    "disk",
    "descriptor",
    "env",
    "docs",
    "checksums",
    "incus-metadata",
    "lxd-metadata",
)


class ImageError(CvcpkgError):
    """An image descriptor is missing, unreadable, or does not conform."""


# ── Path safety ─────────────────────────────────────────────────


def is_safe_relpath(value: str) -> bool:
    """True when *value* is a relative path contained by the image directory.

    Descriptor fields (``disks[].file``, ``importers.*``, ``docs``) are joined
    onto the image directory, so an absolute path or a ``..`` component would
    let a recipe hand out a path outside the prefix.  Reject both.
    """
    if not value or value.startswith(("/", "\\")):
        return False
    p = Path(value)
    if p.is_absolute() or p.drive or p.anchor:
        return False
    return not any(part == ".." for part in p.parts)


# ── The installed-image record ──────────────────────────────────


@dataclass(frozen=True)
class InstalledImage:
    """One installed image package, as read from its ``image.yaml``."""

    name: str
    directory: Path
    data: dict[str, Any]

    # -- descriptor accessors (all tolerant of a partial descriptor) --

    @property
    def descriptor_path(self) -> Path:
        return self.directory / IMAGE_DESCRIPTOR

    @property
    def image(self) -> dict[str, Any]:
        block = self.data.get("image")
        return block if isinstance(block, dict) else {}

    @property
    def boot(self) -> dict[str, Any]:
        block = self.data.get("boot")
        return block if isinstance(block, dict) else {}

    @property
    def access(self) -> dict[str, Any]:
        block = self.data.get("access")
        return block if isinstance(block, dict) else {}

    @property
    def importers(self) -> dict[str, Any]:
        block = self.data.get("importers")
        return block if isinstance(block, dict) else {}

    @property
    def version(self) -> str:
        return str(self.image.get("version", ""))

    @property
    def guest_os(self) -> str:
        return str(self.image.get("guest_os", ""))

    @property
    def guest_arch(self) -> str:
        return str(self.image.get("guest_arch", ""))

    @property
    def guest_release(self) -> str:
        return str(self.image.get("guest_release", ""))

    @property
    def variant(self) -> str:
        return str(self.image.get("variant", ""))

    @property
    def firmware(self) -> str:
        return str(self.boot.get("firmware", ""))

    @property
    def disk_bus(self) -> str:
        return str(self.boot.get("disk_bus", ""))

    @property
    def writable(self) -> bool:
        return bool(self.data.get("writable", False))

    @property
    def disks(self) -> list[dict[str, Any]]:
        disks = self.data.get("disks")
        return [d for d in disks if isinstance(d, dict)] if isinstance(disks, list) else []

    @property
    def primary_disk(self) -> dict[str, Any] | None:
        """The disk a hypervisor should boot: ``role: root``, else the first."""
        disks = self.disks
        if not disks:
            return None
        for d in disks:
            if d.get("role") == "root":
                return d
        return disks[0]

    @property
    def virtual_size_bytes(self) -> int:
        disk = self.primary_disk or {}
        try:
            return int(disk.get("virtual_size_bytes") or 0)
        except (TypeError, ValueError):
            return 0

    def resolve(self, relpath: str) -> Path:
        """Join a descriptor-declared relative path onto the image directory."""
        if not is_safe_relpath(relpath):
            raise ImageError(
                f"{self.descriptor_path}: refusing unsafe path {relpath!r} "
                "(must be relative and stay inside the image directory)"
            )
        return self.directory / relpath

    def role_path(self, role: str) -> Path | None:
        """Absolute path for *role*, or ``None`` if this image has no such role.

        ``None`` covers three cases a shell consumer cannot act on anyway: an
        unknown role name, a role this descriptor does not declare, and a
        declared file that is not on disk.
        """
        rel: str | None = None
        if role == "disk":
            disk = self.primary_disk
            rel = str(disk.get("file", "")) if disk else None
        elif role == "descriptor":
            rel = IMAGE_DESCRIPTOR
        elif role == "env":
            rel = IMAGE_ENV_FILE
        elif role == "checksums":
            rel = CHECKSUM_FILE
        elif role == "docs":
            rel = str(self.data.get("docs", "")) or None
        elif role == "incus-metadata":
            rel = str(self.importers.get("incus", "")) or None
        elif role == "lxd-metadata":
            rel = str(self.importers.get("lxd", "")) or None
        else:
            return None
        if not rel or not is_safe_relpath(rel):
            return None
        path = self.directory / rel
        return path if path.exists() else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly summary used by ``image ls --json`` / ``info --json``."""
        return {
            "name": self.name,
            "directory": str(self.directory),
            "version": self.version,
            "guest_os": self.guest_os,
            "guest_arch": self.guest_arch,
            "guest_release": self.guest_release,
            "variant": self.variant,
            "firmware": self.firmware,
            "disk_bus": self.disk_bus,
            "virtual_size_bytes": self.virtual_size_bytes,
            "writable": self.writable,
            "descriptor": str(self.descriptor_path),
        }


# ── Loading & discovery ─────────────────────────────────────────


def _looks_like_descriptor(doc: Any) -> bool:
    """Cheap shape test so an unrelated ``share/<pkg>/image.yaml`` is skipped."""
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("image"), dict)
        and bool(doc["image"].get("package"))
    )


def load_image(directory: Path | str) -> InstalledImage:
    """Read the ``image.yaml`` in *directory* into an :class:`InstalledImage`.

    Raises :class:`ImageError` if the descriptor is missing, unparseable, of an
    unknown ``schema_version``, or names a different package than its directory
    (the directory name IS the addressing key, so a mismatch would make the
    image unaddressable).
    """
    directory = Path(directory)
    descriptor = directory / IMAGE_DESCRIPTOR
    if not descriptor.is_file():
        raise ImageError(f"no {IMAGE_DESCRIPTOR} in {directory}")
    try:
        with open(descriptor, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ImageError(f"{descriptor}: cannot read image descriptor: {exc}") from exc

    if not _looks_like_descriptor(doc):
        raise ImageError(f"{descriptor}: not a cvcpkg image descriptor (no image.package)")

    try:
        version = int(doc.get("schema_version", 0))
    except (TypeError, ValueError):
        version = 0
    if version != IMAGE_SCHEMA_VERSION:
        raise ImageError(
            f"{descriptor}: schema_version {doc.get('schema_version')!r} is not supported "
            f"(this cvcpkg understands {IMAGE_SCHEMA_VERSION}) — upgrade cvcpkg"
        )

    package = str(doc["image"]["package"])
    if package != directory.name:
        raise ImageError(
            f"{descriptor}: image.package is {package!r} but the directory is "
            f"{directory.name!r} — the directory name is the addressing key, so "
            "they must agree"
        )
    return InstalledImage(name=package, directory=directory, data=doc)


def discover_images(prefix: Path | str) -> list[InstalledImage]:
    """Every installed image under *prefix*, sorted by name.

    A glob over ``<prefix>/share/*/image.yaml``.  Directories whose
    ``image.yaml`` is not a cvcpkg image descriptor (or is unreadable) are
    skipped rather than fatal: ``image ls`` must keep working when one package
    in a shared prefix ships an unrelated file of that name.
    """
    share = Path(prefix) / SHARE_DIR
    if not share.is_dir():
        return []
    found: list[InstalledImage] = []
    for descriptor in sorted(share.glob(f"*/{IMAGE_DESCRIPTOR}")):
        try:
            found.append(load_image(descriptor.parent))
        except ImageError:
            continue
    return found


def find_image(prefix: Path | str, name: str) -> InstalledImage | None:
    """The installed image called *name* under *prefix*, or ``None``.

    Unlike :func:`discover_images` this is a direct lookup by directory name,
    so a descriptor that exists but is malformed raises rather than vanishing —
    a caller that asked for this image by name needs to hear why.
    """
    directory = Path(prefix) / SHARE_DIR / name
    if not (directory / IMAGE_DESCRIPTOR).is_file():
        return None
    return load_image(directory)


# ── image.env ───────────────────────────────────────────────────
#
# The flat KEY=value view.  An Incus/LXD cluster node reliably has neither jq
# nor yq nor pkg-config, but every /bin/sh can source a KEY=value file.  The
# recipe writes this to disk with paths RELATIVE to the image directory (so it
# is `. `-sourceable after a cd); `cvcpkg image env` regenerates it from the
# same descriptor with ABSOLUTE paths, because the CLI knows the prefix and its
# output is meant for `eval` from an arbitrary working directory.


def env_map(image: InstalledImage, *, absolute: bool = True) -> dict[str, str]:
    """Descriptor → the ``CVCPKG_IMAGE_*`` environment mapping.

    Empty values are omitted so ``eval`` never sets a variable to the empty
    string when the descriptor simply did not say.
    """
    boot = image.boot
    access = image.access

    def _path(rel: Any) -> str:
        rel = str(rel or "")
        if not rel or not is_safe_relpath(rel):
            return ""
        return str(image.directory / rel) if absolute else rel

    disk = image.primary_disk or {}
    pairs: list[tuple[str, Any]] = [
        ("CVCPKG_IMAGE_NAME", image.name),
        ("CVCPKG_IMAGE_VERSION", image.version),
        ("CVCPKG_IMAGE_DIR", str(image.directory) if absolute else "."),
        ("CVCPKG_IMAGE_DISK", _path(disk.get("file"))),
        ("CVCPKG_IMAGE_DISK_FORMAT", disk.get("format", "")),
        ("CVCPKG_IMAGE_DISK_BUS", boot.get("disk_bus", "")),
        ("CVCPKG_IMAGE_FIRMWARE", boot.get("firmware", "")),
        ("CVCPKG_IMAGE_NET_MODEL", boot.get("net_model", "")),
        ("CVCPKG_IMAGE_CONSOLE", boot.get("console", "")),
        ("CVCPKG_IMAGE_SECUREBOOT", boot.get("secureboot", "")),
        ("CVCPKG_IMAGE_CPU_MIN", boot.get("cpu_min", "")),
        ("CVCPKG_IMAGE_MEMORY_MIN_MIB", boot.get("memory_min_mib", "")),
        ("CVCPKG_IMAGE_DISK_MIN_GIB", boot.get("disk_min_gib", "")),
        ("CVCPKG_IMAGE_SSH_USER", access.get("ssh_user", "")),
        ("CVCPKG_IMAGE_WORK_DIR", access.get("work_dir", "")),
        ("CVCPKG_IMAGE_GUEST_OS", image.guest_os),
        ("CVCPKG_IMAGE_GUEST_ARCH", image.guest_arch),
        ("CVCPKG_IMAGE_GUEST_RELEASE", image.guest_release),
        ("CVCPKG_IMAGE_VARIANT", image.variant),
        ("CVCPKG_IMAGE_WRITABLE", image.data.get("writable", "")),
        ("CVCPKG_IMAGE_INCUS_METADATA", _path(image.importers.get("incus"))),
        ("CVCPKG_IMAGE_LXD_METADATA", _path(image.importers.get("lxd"))),
        ("CVCPKG_IMAGE_DOCS", _path(image.data.get("docs"))),
        ("CVCPKG_IMAGE_CHECKSUMS", _path(CHECKSUM_FILE)),
    ]
    out: dict[str, str] = {}
    for key, value in pairs:
        text = "true" if value is True else "false" if value is False else str(value or "")
        if text:
            out[key] = text
    return out


def env_script(image: InstalledImage, *, absolute: bool = True) -> str:
    """``env_map`` rendered for ``eval "$(cvcpkg image env NAME)"``."""
    return "".join(f"{k}={shlex.quote(v)}\n" for k, v in env_map(image, absolute=absolute).items())


# ── Verification ────────────────────────────────────────────────


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def parse_sha256sums(text: str) -> list[tuple[str, str]]:
    """Parse ``sha256sum``/``sha256sum -c`` output into ``(hexdigest, path)``.

    Accepts both the text (``<hex>  <path>``) and binary (``<hex> *<path>``)
    forms.  Blank lines and ``#`` comments are ignored.
    """
    entries: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].lstrip("*").strip()
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            entries.append((digest, name))
    return entries


def verify_image(image: InstalledImage) -> list[tuple[str, str, str]]:
    """Re-hash the bytes on disk.  Returns ``(relpath, status, detail)`` rows.

    ``status`` is ``OK``, ``FAILED`` (hash mismatch), ``MISSING`` (declared but
    absent) or ``SKIPPED`` (unsafe path in the checksum file).

    This is NOT redundant with the installer's download-time sha256: that check
    covers the archive, once, at download time.  This one covers a 1 GiB payload
    six months later, on the NFS mount where it actually lives.
    """
    checksums = image.directory / CHECKSUM_FILE
    if checksums.is_file():
        expected = parse_sha256sums(checksums.read_text(encoding="utf-8"))
    else:
        # No SHA256SUMS: fall back to the per-disk digests in the descriptor so
        # `verify` still means something on an older or hand-made image.
        expected = [
            (str(d["sha256"]).lower(), str(d.get("file", "")))
            for d in image.disks
            if d.get("sha256") and d.get("file")
        ]
    if not expected:
        raise ImageError(
            f"{image.name}: nothing to verify — no {CHECKSUM_FILE} and no "
            "disks[].sha256 in the descriptor"
        )

    rows: list[tuple[str, str, str]] = []
    for digest, relpath in expected:
        if not is_safe_relpath(relpath):
            rows.append((relpath, "SKIPPED", "unsafe path in checksum file"))
            continue
        target = image.directory / relpath
        if not target.is_file():
            rows.append((relpath, "MISSING", "declared but not present"))
            continue
        actual = sha256_file(target)
        if actual == digest:
            rows.append((relpath, "OK", actual))
        else:
            rows.append((relpath, "FAILED", f"expected {digest}, got {actual}"))
    return rows


# ── Export ──────────────────────────────────────────────────────


def export_filename(image: InstalledImage, source: Path) -> str:
    """A human-meaningful name for a file copied out of the prefix.

    Role-based names are right *inside* the prefix (derivable from the package
    name) and wrong outside it, where ``disk.qcow2`` says nothing.  Restore the
    identity on the way out: ``haiku-image-1.0.0-beta.5+cvc.1.qcow2``.
    """
    name, _, suffix = source.name.partition(".")
    stem = f"{image.name}-{image.version}" if image.version else image.name
    if not suffix:
        return f"{stem}-{name}"
    return f"{stem}.{suffix}"


def _reflink_copy(source: Path, dest: Path) -> bool:
    """Try a copy-on-write clone.  True if it happened, False to fall back."""
    if sys.platform.startswith("linux"):
        argv = ["cp", "--reflink=auto", "--", str(source), str(dest)]
    elif sys.platform == "darwin":
        argv = ["cp", "-c", "--", str(source), str(dest)]
    else:
        return False
    if shutil.which(argv[0]) is None:
        return False
    try:
        return subprocess.run(argv, capture_output=True).returncode == 0
    except OSError:
        return False


def export_image(image: InstalledImage, dest_dir: Path | str, *, role: str = "disk") -> Path:
    """Copy the *role* artifact out of the prefix into *dest_dir*.

    You never hand a hypervisor a path inside a cvcpkg prefix: a later
    ``cvcpkg install`` may replace those bytes underneath a running VM.  Export
    first, boot from the copy.
    """
    source = image.role_path(role)
    if source is None:
        raise ImageError(f"{image.name}: no '{role}' artifact to export")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / export_filename(image, source)
    if dest.resolve() == source.resolve():
        raise ImageError(f"{image.name}: refusing to export onto itself ({dest})")
    if not _reflink_copy(source, dest):
        shutil.copy2(source, dest)
    return dest


# ── Staged-tree enforcement (pack-time preflight) ───────────────


def check_staged_image_tree(install_dir: Path | str, package: str) -> list[str]:
    """Validate a ``kind: image`` recipe's STAGED tree before it is archived.

    A ``kind: image`` bundle must contain nothing outside
    ``share/<package>/``, must carry an ``image.yaml`` there, and every
    ``disks[].file`` it declares must actually exist.  Without this the layout
    is a comment: image package #2 repeats the prefix-root mistake and nobody
    notices until two images are installed side by side and clobber each other.

    Returns a list of human-readable problems (empty when the tree is good).
    """
    install_dir = Path(install_dir)
    expected_rel = Path(SHARE_DIR) / package
    image_dir = install_dir / expected_rel
    errors: list[str] = []

    strays: list[str] = []
    for path in sorted(install_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(install_dir)
        if expected_rel not in rel.parents:
            strays.append(rel.as_posix())
    if strays:
        shown = ", ".join(strays[:8]) + (" ..." if len(strays) > 8 else "")
        errors.append(
            f"kind: image stages {len(strays)} file(s) outside "
            f"{expected_rel.as_posix()}/: {shown} — an image package owns exactly "
            "one directory named after itself, so generically-named files "
            "(metadata.yaml, README.md) cannot collide with the next image"
        )

    if not (image_dir / IMAGE_DESCRIPTOR).is_file():
        errors.append(f"kind: image has no {expected_rel.as_posix()}/{IMAGE_DESCRIPTOR}")
        return errors

    try:
        image = load_image(image_dir)
    except ImageError as exc:
        errors.append(str(exc))
        return errors

    errors.extend(validate_descriptor(image.data, source=str(image.descriptor_path)))

    for disk in image.disks:
        rel = str(disk.get("file", ""))
        if not rel:
            continue
        if not is_safe_relpath(rel):
            errors.append(f"{image.descriptor_path}: disks[].file {rel!r} escapes the image dir")
        elif not (image_dir / rel).is_file():
            errors.append(f"{image.descriptor_path}: disks[].file {rel!r} does not exist")
    return errors


def validate_descriptor(doc: Any, *, source: str = "image.yaml") -> list[str]:
    """Validate an ``image.yaml`` document against the bundled JSON Schema."""
    from jsonschema import Draft202012Validator

    from cvcpkg.validation import load_schema

    schema = load_schema("image")
    validator = Draft202012Validator(schema)
    return [
        f"{source}: {'.'.join(str(p) for p in e.absolute_path)}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda x: list(x.absolute_path))
    ]
