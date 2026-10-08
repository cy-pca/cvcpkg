# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Dynamic-linkage metadata of ELF and Mach-O files, and the relocatability gate.

A bundle is unpacked into an arbitrary prefix, so every path the dynamic loader
follows must either be relative to the object (``$ORIGIN``, ``@rpath``,
``@loader_path``, ``@executable_path``) or belong to the operating system.  A
path into the machine that BUILT the bundle — the job's scratch prefix, a CI
runner's workspace, the build host's package manager — exists nowhere else, so
the program fails to start once installed:

    cmake: error while loading shared libraries: libcurl.so.4.8.0: cannot open
    shared object file   (RUNPATH /tmp/cvcpkg-builder/cvcpkg-job-cmake-.../lib)

    dyld: Library not loaded: /private/var/folders/.../cvcpkg-curl-.../install/
    lib/libcurl.4.dylib

Nothing used to check for this, and the relocation pass only ever looked at
``lib/`` — so the executables in ``bin/`` shipped with builder paths for several
revisions of cmake on every platform.  :func:`audit_tree` reads the linkage of
every ELF/Mach-O object in a staged bundle and reports each such path, and
:func:`builder_path_reason` is the shared definition of "a builder path" that
the relocation pass in :mod:`cvcpkg.builder` rewrites against.

Like :mod:`cvcpkg.glibc`, the files are parsed here in Python instead of shelling
out to readelf/objdump/otool: those are not installed on every builder (no otool
off macOS, no readelf on a stock macOS or BSD), and a gate that silently skips
itself where its tool is missing is worse than none.
"""

from __future__ import annotations

import os
import re
import struct
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

# ── ELF ─────────────────────────────────────────────────────────

_ELF_MAGIC = b"\x7fELF"
_ET_EXEC, _ET_DYN = 2, 3
_PT_LOAD, _PT_DYNAMIC, _PT_INTERP = 1, 2, 3
_DT_NULL, _DT_NEEDED, _DT_STRTAB, _DT_STRSZ = 0, 1, 5, 10
_DT_SONAME, _DT_RPATH, _DT_RUNPATH = 14, 15, 29

# ── Mach-O ──────────────────────────────────────────────────────

_MH_MAGIC, _MH_MAGIC_64 = 0xFEEDFACE, 0xFEEDFACF
_FAT_MAGIC, _FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF
_MH_EXECUTE, _MH_DYLIB, _MH_BUNDLE = 0x2, 0x6, 0x8
_LC_REQ_DYLD = 0x80000000
_LC_LOAD_DYLIB = 0xC
_LC_ID_DYLIB = 0xD
_LC_LAZY_LOAD_DYLIB = 0x20
_LC_LOAD_WEAK_DYLIB = 0x18 | _LC_REQ_DYLD
_LC_RPATH = 0x1C | _LC_REQ_DYLD
_LC_REEXPORT_DYLIB = 0x1F | _LC_REQ_DYLD
_LC_LOAD_UPWARD_DYLIB = 0x23 | _LC_REQ_DYLD
_LOAD_COMMANDS = {
    _LC_LOAD_DYLIB,
    _LC_LOAD_WEAK_DYLIB,
    _LC_REEXPORT_DYLIB,
    _LC_LAZY_LOAD_DYLIB,
    _LC_LOAD_UPWARD_DYLIB,
}
# A fat header's arch count, read where a Java class file keeps its version
# (both start 0xCAFEBABE).  file(1) uses the same cut-off: a class file's major
# version is >= 45, no universal binary has that many slices.
_FAT_MAX_ARCHS = 20


@dataclass(frozen=True)
class Linkage:
    """What the dynamic loader reads from one object.

    ``needed`` is DT_NEEDED (ELF) or every LC_*_DYLIB load (Mach-O), ``rpaths``
    the DT_RUNPATH/DT_RPATH entries or LC_RPATHs, ``soname`` the DT_SONAME or
    LC_ID_DYLIB.  ``dt_rpath`` is set when an ELF search path is a legacy
    DT_RPATH with no DT_RUNPATH, so an editor can keep it one (patchelf
    ``--force-rpath``).  For a universal Mach-O the slices are merged.
    """

    format: str  # "elf" | "macho"
    kind: str  # "executable" | "shared" | "bundle"
    needed: tuple[str, ...] = ()
    rpaths: tuple[str, ...] = ()
    soname: str | None = None
    dt_rpath: bool = False


def read_linkage(path: Path | str) -> Linkage | None:
    """Linkage of the ELF or Mach-O object at *path*; None for anything else.

    Only objects the loader maps on their own are returned: ELF executables and
    shared objects, Mach-O executables, dylibs and bundles.  Relocatable
    objects, static archives, dSYM companions, scripts and data are None, as is
    a file too damaged to parse — callers walk whole install trees and must not
    trip over a stray ``.o`` or a truncated test fixture.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if head[:4] == _ELF_MAGIC:
                return _read_elf(f)
            if len(head) >= 8:
                return _read_macho_any(f, head)
    except (OSError, struct.error, ValueError, IndexError):
        return None
    return None


@dataclass
class _ElfDynamic:
    kind: str
    tags: list[tuple[int, int]]
    str_off: int | None = None  # file offset of the dynamic string table
    strings: bytes = b""

    def string(self, at: int) -> str:
        stop = self.strings.find(b"\0", at)
        return self.strings[at : stop if stop >= 0 else len(self.strings)].decode(
            "utf-8", "replace"
        )


def _parse_elf(f: BinaryIO) -> _ElfDynamic | None:
    f.seek(0)
    ident = f.read(16)
    is64 = ident[4] == 2
    end = "<" if ident[5] == 1 else ">"
    if is64:
        hdr = f.read(48)
        e_type = struct.unpack_from(end + "H", hdr, 0)[0]
        e_phoff = struct.unpack_from(end + "Q", hdr, 16)[0]
        e_phentsize, e_phnum = struct.unpack_from(end + "HH", hdr, 38)
        ph_fmt = end + "IIQQQQQQ"  # type flags offset vaddr paddr filesz memsz align
    else:
        hdr = f.read(36)
        e_type = struct.unpack_from(end + "H", hdr, 0)[0]
        e_phoff = struct.unpack_from(end + "I", hdr, 12)[0]
        e_phentsize, e_phnum = struct.unpack_from(end + "HH", hdr, 26)
        ph_fmt = end + "IIIIIIII"  # type offset vaddr paddr filesz memsz flags align
    if e_type not in (_ET_EXEC, _ET_DYN) or not e_phoff or not e_phnum:
        return None

    f.seek(e_phoff)
    table = f.read(e_phentsize * e_phnum)
    loads: list[tuple[int, int, int]] = []  # (vaddr, offset, filesz)
    dynamic: tuple[int, int] | None = None  # (offset, filesz)
    has_interp = False
    for i in range(e_phnum):
        ph = struct.unpack_from(ph_fmt, table, i * e_phentsize)
        if is64:
            p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = ph[:6]
        else:
            p_type, p_offset, p_vaddr, _paddr, p_filesz = ph[:5]
        if p_type == _PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == _PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
        elif p_type == _PT_INTERP:
            has_interp = True
    # ET_DYN is a shared object or a PIE executable; only the latter asks for
    # an interpreter.
    kind = "executable" if e_type == _ET_EXEC or has_interp else "shared"
    if dynamic is None:
        return _ElfDynamic(kind, [])  # static executable

    f.seek(dynamic[0])
    blob = f.read(dynamic[1])
    ent_fmt = end + ("qQ" if is64 else "iI")
    ent_size = struct.calcsize(ent_fmt)
    tags: list[tuple[int, int]] = []
    for off in range(0, len(blob) - ent_size + 1, ent_size):
        tag, val = struct.unpack_from(ent_fmt, blob, off)
        if tag == _DT_NULL:
            break
        tags.append((tag, val))
    dyn = _ElfDynamic(kind, tags)
    strtab = next((v for t, v in tags if t == _DT_STRTAB), None)
    strsz = next((v for t, v in tags if t == _DT_STRSZ), None)
    if strtab is None or not strsz:
        return dyn
    # DT_STRTAB is an address; map it to a file offset through the PT_LOAD
    # segment that contains it.
    dyn.str_off = next(
        (off + strtab - va for va, off, sz in loads if va <= strtab < va + sz),
        None,
    )
    if dyn.str_off is not None:
        f.seek(dyn.str_off)
        dyn.strings = f.read(strsz)
    return dyn


def _read_elf(f: BinaryIO) -> Linkage | None:
    dyn = _parse_elf(f)
    if dyn is None:
        return None
    if not dyn.strings:
        return Linkage("elf", dyn.kind)
    tags = dyn.tags
    needed = tuple(dyn.string(v) for t, v in tags if t == _DT_NEEDED)
    # The loader searches DT_RUNPATH and ignores DT_RPATH when both are
    # present; report both — either one shipping a builder path is a defect.
    rpaths: list[str] = []
    for t, v in tags:
        if t in (_DT_RUNPATH, _DT_RPATH):
            rpaths.extend(e for e in dyn.string(v).split(":") if e)
    soname = next((dyn.string(v) for t, v in tags if t == _DT_SONAME), None)
    present = {t for t, _ in tags}
    dt_rpath = _DT_RPATH in present and _DT_RUNPATH not in present
    return Linkage("elf", dyn.kind, needed, tuple(rpaths), soname, dt_rpath)


def rewrite_elf_rpath(path: Path | str, value: str) -> bool:
    """Set every DT_RUNPATH/DT_RPATH of the ELF at *path* to *value*, in place.

    Only ever overwrites the existing string in ``.dynstr`` (NUL-padded), so it
    returns False without touching the file when *value* is longer than the
    string it replaces, or when there is no RPATH to rewrite.  An empty *value*
    leaves an empty search path.  In-place is the edit that is always safe: it
    adds no segment (NetBSD's ld.elf_so refuses objects with a third PT_LOAD,
    which is what growing ``.dynstr`` with patchelf produces) and needs no tool,
    so the relocation pass works on a builder without patchelf.
    """
    with open(path, "r+b") as f:
        if f.read(4) != _ELF_MAGIC:
            return False
        dyn = _parse_elf(f)
        if dyn is None or dyn.str_off is None:
            return False
        slots = sorted({v for t, v in dyn.tags if t in (_DT_RUNPATH, _DT_RPATH)})
        if not slots:
            return False
        encoded = value.encode()
        for at in slots:
            if len(encoded) > len(dyn.string(at).encode()):
                return False
        for at in slots:
            old_len = len(dyn.string(at).encode())
            f.seek(dyn.str_off + at)
            f.write(encoded + b"\0" * (old_len - len(encoded) + 1))
    return True


def _read_macho_any(f: BinaryIO, head: bytes) -> Linkage | None:
    magic_be = struct.unpack(">I", head[:4])[0]
    if magic_be in (_FAT_MAGIC, _FAT_MAGIC_64):
        nfat = struct.unpack(">I", head[4:8])[0]
        if not 0 < nfat < _FAT_MAX_ARCHS:
            return None  # a Java class file
        is64 = magic_be == _FAT_MAGIC_64
        entry = 32 if is64 else 20
        f.seek(8)
        table = f.read(entry * nfat)
        slices = []
        for i in range(nfat):
            if is64:
                offset = struct.unpack_from(">Q", table, i * entry + 8)[0]
            else:
                offset = struct.unpack_from(">I", table, i * entry + 8)[0]
            sl = _read_macho(f, offset)
            if sl is not None:
                slices.append(sl)
        if not slices:
            return None
        return Linkage(
            "macho",
            slices[0].kind,
            _dedupe(e for s in slices for e in s.needed),
            _dedupe(e for s in slices for e in s.rpaths),
            next((s.soname for s in slices if s.soname), None),
        )
    return _read_macho(f, 0)


def _read_macho(f: BinaryIO, base: int) -> Linkage | None:
    f.seek(base)
    raw = f.read(4)
    if len(raw) < 4:
        return None
    if struct.unpack("<I", raw)[0] in (_MH_MAGIC, _MH_MAGIC_64):
        end = "<"
    elif struct.unpack(">I", raw)[0] in (_MH_MAGIC, _MH_MAGIC_64):
        end = ">"
    else:
        return None
    is64 = struct.unpack(end + "I", raw)[0] == _MH_MAGIC_64
    hdr = f.read(24)
    _cpu, _sub, filetype, ncmds, sizeofcmds, _flags = struct.unpack(end + "6I", hdr)
    kind = {_MH_EXECUTE: "executable", _MH_DYLIB: "shared", _MH_BUNDLE: "bundle"}.get(filetype)
    if kind is None:
        return None
    f.seek(base + (32 if is64 else 28))
    cmds = f.read(sizeofcmds)
    needed: list[str] = []
    rpaths: list[str] = []
    soname: str | None = None
    off = 0
    for _ in range(ncmds):
        if off + 8 > len(cmds):
            break
        cmd, cmdsize = struct.unpack_from(end + "II", cmds, off)
        if cmdsize < 8:
            break
        if cmd in _LOAD_COMMANDS or cmd in (_LC_ID_DYLIB, _LC_RPATH):
            name_off = struct.unpack_from(end + "I", cmds, off + 8)[0]
            body = cmds[off + name_off : off + cmdsize]
            stop = body.find(b"\0")
            name = body[: stop if stop >= 0 else len(body)].decode("utf-8", "replace")
            if cmd == _LC_RPATH:
                rpaths.append(name)
            elif cmd == _LC_ID_DYLIB:
                soname = name
            else:
                needed.append(name)
        off += cmdsize
    return Linkage("macho", kind, tuple(needed), tuple(rpaths), soname)


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def iter_objects(root: Path) -> Iterator[tuple[Path, Linkage]]:
    """Every ELF/Mach-O object under *root* (symlinks not followed), sorted."""
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            link = read_linkage(p)
            if link is not None:
                yield p, link


# ── What counts as a builder path ───────────────────────────────

# Scratch directories cvcpkg itself creates: the builder's work root, its
# per-job / per-prefix dirs and every tempfile.mkdtemp(prefix="cvcpkg-<x>-")
# (8-character random suffix).  Matched as one path component.
_CVCPKG_TEMP_COMPONENT = re.compile(r"^cvcpkg-(builder|.+-[a-z0-9_]{8})$")

# Roots that only ever hold throwaway build state — the OS temp dirs and the
# workspaces of CI runners (GitHub-hosted macOS/Linux, container jobs).  A
# library loaded from one of these on an end user's machine is at best missing
# and at worst planted (/tmp is world-writable).
_EPHEMERAL_ROOTS = (
    "/tmp/",
    "/var/tmp/",
    "/private/tmp/",
    "/var/folders/",
    "/private/var/folders/",
    "/home/runner/work/",
    "/Users/runner/work/",
    "/__w/",
    "/github/workspace/",
)

# Mach-O references are allowed to be absolute only into the OS itself.  A
# clean Mac has nothing else at a fixed path — not Homebrew (/opt/homebrew,
# /usr/local), not MacPorts (/opt/local) — so anything outside these is a
# dependency the bundle does not carry and did not declare.
MACOS_SYSTEM_ROOTS = ("/usr/lib/", "/System/")


def _norm(p: str) -> str:
    return p.rstrip("/") + "/"


def _ephemeral_roots() -> tuple[str, ...]:
    """Fixed ephemeral roots plus the ones this host is configured with."""
    extra = [tempfile.gettempdir()]
    for var in ("TMPDIR", "RUNNER_TEMP", "RUNNER_WORKSPACE", "GITHUB_WORKSPACE"):
        if os.environ.get(var):
            extra.append(os.environ[var])
    out = list(_EPHEMERAL_ROOTS)
    for e in extra:
        if e and os.path.isabs(e) and len(e.strip("/")) > 0:
            out.append(_norm(e))
            real = os.path.realpath(e)
            if real != e:
                out.append(_norm(real))
    return tuple(dict.fromkeys(out))


def _expand_prefixes(prefixes: Sequence[Path | str]) -> tuple[str, ...]:
    """*prefixes* as normalised strings, plus their realpaths.

    macOS hands out /var/folders/... while the linker records the resolved
    /private/var/folders/..., so both spellings must match.
    """
    out: list[str] = []
    for p in prefixes:
        if not p:
            continue
        s = str(p)
        out.append(_norm(s))
        out.append(_norm(os.path.realpath(s)))
    return tuple(dict.fromkeys(out))


def relative_to_prefixes(value: str, prefixes: Sequence[Path | str]) -> str | None:
    """The part of *value* below the longest of *prefixes* containing it, or None.

    ``"/tmp/job/prefix/lib"`` against ``["/tmp/job/prefix"]`` is ``"lib"``; a
    value equal to a prefix is ``"."``.
    """
    probe = _norm(value)
    best: str | None = None
    for p in _expand_prefixes(prefixes):
        if probe.startswith(p) and (best is None or len(p) > len(best)):
            best = p
    if best is None:
        return None
    return probe[len(best) :].strip("/") or "."


def is_relative_entry(value: str) -> bool:
    """True for loader-relative entries: ``$ORIGIN``, ``@rpath``, ``@loader_path`` …"""
    return value.startswith(("$ORIGIN", "${ORIGIN}", "@"))


def builder_path_reason(
    value: str, temp_prefixes: Sequence[Path | str] = (), *, _roots: tuple[str, ...] | None = None
) -> str | None:
    """Why *value* is a path into the build machine, or None if it is not one.

    *temp_prefixes* are this build's own scratch prefixes (deps, build-tool and
    install dirs); the rest is recognised by shape: cvcpkg's temp-dir names,
    OS temp dirs and CI runner workspaces.
    """
    if not value.startswith("/"):
        return None
    probe = _norm(value)
    for p in _expand_prefixes(temp_prefixes):
        if probe.startswith(p):
            return "this build's scratch prefix"
    if any(_CVCPKG_TEMP_COMPONENT.match(c) for c in value.split("/")):
        return "a cvcpkg build directory"
    for root in _roots if _roots is not None else _ephemeral_roots():
        if probe.startswith(root):
            return "a temporary or CI-workspace directory"
    return None


# ── The gate ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    path: str  # object, relative to the audited root
    field: str  # RUNPATH, NEEDED, LC_RPATH, LC_LOAD_DYLIB, ...
    value: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.field} {self.value} ({self.reason})"


@dataclass
class AuditResult:
    objects: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _macos_reason(value: str, builder: str | None) -> str | None:
    if builder or not value.startswith("/"):
        return builder
    if value.startswith(MACOS_SYSTEM_ROOTS):
        return None
    if value.startswith(("/opt/homebrew/", "/usr/local/")):
        return "the build host's Homebrew"
    if value.startswith("/opt/local/"):
        return "the build host's MacPorts"
    return "outside the OS; a clean Mac does not have it"


def audit_tree(
    root: Path,
    platform: str,
    temp_prefixes: Sequence[Path | str] = (),
    allow: Sequence[str] = (),
) -> AuditResult:
    """Every loader path under *root* that will not exist once it is installed.

    ELF (linux, the BSDs, haiku): RUNPATH/RPATH entries, and NEEDED/SONAME
    values that carry a directory, must not point into the build machine (see
    :func:`builder_path_reason`).  Absolute system dirs (``/usr/local/lib`` on
    FreeBSD, ``/usr/pkg/lib`` on NetBSD) stay legal there.

    Mach-O (macos): LC_RPATH, LC_*_DYLIB and LC_ID_DYLIB must be loader-relative
    or inside the OS (``/usr/lib``, ``/System``) — a builder path, Homebrew or
    MacPorts is reported.

    *allow* lists path prefixes a recipe vouches for (``package.linkage_allow``)
    — e.g. a vendor framework installed at a fixed location.  It never excuses a
    builder path.  Windows and wasm bundles have nothing to check and return an
    empty result.
    """
    result = AuditResult()
    if platform in ("windows", "wasm", "wasm-mt", "wasi", "cosmo", "any"):
        return result
    roots = _ephemeral_roots()
    allowed = tuple(a for a in allow if a)

    def check(rel: str, fld: str, value: str, macho: bool) -> None:
        if not value or is_relative_entry(value):
            return
        builder = builder_path_reason(value, temp_prefixes, _roots=roots)
        reason = _macos_reason(value, builder) if macho else builder
        # The allowlist vouches for fixed install locations; nothing can vouch
        # for a builder path.
        if reason and not (builder is None and allowed and value.startswith(allowed)):
            result.findings.append(Finding(rel, fld, value, reason))

    for obj, link in iter_objects(root):
        result.objects += 1
        rel = obj.relative_to(root).as_posix()
        macho = link.format == "macho"
        rp_field = "LC_RPATH" if macho else ("RPATH" if link.dt_rpath else "RUNPATH")
        for e in link.rpaths:
            check(rel, rp_field, e, macho)
        for n in link.needed:
            if macho or "/" in n:
                check(rel, "LC_LOAD_DYLIB" if macho else "NEEDED", n, macho)
        if link.soname and (macho or "/" in link.soname):
            check(rel, "LC_ID_DYLIB" if macho else "SONAME", link.soname, macho)
    return result


def format_findings(findings: Sequence[Finding], limit: int = 40) -> str:
    lines = [f"  {f}" for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"  ... and {len(findings) - limit} more")
    return "\n".join(lines)
