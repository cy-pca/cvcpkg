"""Minimal, loader-shaped ELF and Mach-O files for linkage tests.

Real enough for :mod:`cvcpkg.linkage` (and readelf/otool) to parse — headers,
program headers / load commands, a dynamic section and its string table — but
with no code, so they can be generated on any host.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from pathlib import Path

# ── ELF ─────────────────────────────────────────────────────────

_ET = {"exec": 2, "dyn": 3, "pie": 3, "rel": 1}
_DT_NEEDED, _DT_STRTAB, _DT_STRSZ, _DT_SONAME, _DT_RPATH, _DT_RUNPATH = 1, 5, 10, 14, 15, 29


def elf(
    path: Path,
    *,
    kind: str = "exec",
    needed: Sequence[str] = (),
    runpath: str | None = None,
    rpath: str | None = None,
    soname: str | None = None,
    bits: int = 64,
    little: bool = True,
) -> Path:
    """Write an ELF executable ("exec"/"pie"), shared object ("dyn") or ".o" ("rel")."""
    e = "<" if little else ">"
    is64 = bits == 64
    ehsize, phentsize = (64, 56) if is64 else (52, 32)
    interp = kind in ("exec", "pie")
    nph = 2 + (1 if interp else 0)
    base = 0x400000

    strtab = bytearray(b"\0")

    def add(s: str) -> int:
        off = len(strtab)
        strtab.extend(s.encode() + b"\0")
        return off

    interp_s = b"/lib64/ld-linux-x86-64.so.2\0"
    dyn: list[tuple[int, int]] = [(_DT_NEEDED, add(n)) for n in needed]
    if soname is not None:
        dyn.append((_DT_SONAME, add(soname)))
    if rpath is not None:
        dyn.append((_DT_RPATH, add(rpath)))
    if runpath is not None:
        dyn.append((_DT_RUNPATH, add(runpath)))

    ph_off = ehsize
    interp_off = ph_off + nph * phentsize
    str_off = interp_off + (len(interp_s) if interp else 0)
    dyn_off = (str_off + len(strtab) + 7) & ~7
    dyn.extend([(_DT_STRTAB, base + str_off), (_DT_STRSZ, len(strtab)), (0, 0)])
    ent = struct.Struct(e + ("qQ" if is64 else "iI"))
    dyn_blob = b"".join(ent.pack(t, v) for t, v in dyn)
    total = dyn_off + len(dyn_blob)

    ident = b"\x7fELF" + bytes([2 if is64 else 1, 1 if little else 2, 1]) + b"\0" * 9
    if is64:
        hdr = ident + struct.pack(
            e + "HHIQQQIHHHHHH",
            _ET[kind],
            0x3E,
            1,
            0,
            ph_off,
            0,
            0,
            ehsize,
            phentsize,
            nph,
            64,
            0,
            0,
        )
        ph = struct.Struct(e + "IIQQQQQQ")

        def phdr(t, off, size):  # type flags offset vaddr paddr filesz memsz align
            return ph.pack(t, 5, off, base + off, base + off, size, size, 8)

    else:
        hdr = ident + struct.pack(
            e + "HHIIIIIHHHHHH", _ET[kind], 3, 1, 0, ph_off, 0, 0, ehsize, phentsize, nph, 40, 0, 0
        )
        ph = struct.Struct(e + "IIIIIIII")

        def phdr(t, off, size):  # type offset vaddr paddr filesz memsz flags align
            return ph.pack(t, off, base + off, base + off, size, size, 5, 8)

    phs = phdr(1, 0, total)  # PT_LOAD over the whole file
    if interp:
        phs += phdr(3, interp_off, len(interp_s))
    phs += phdr(2, dyn_off, len(dyn_blob))
    body = hdr + phs + (interp_s if interp else b"") + bytes(strtab)
    body += b"\0" * (dyn_off - len(body)) + dyn_blob
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


# ── Mach-O ──────────────────────────────────────────────────────

_MH = {"execute": 0x2, "dylib": 0x6, "bundle": 0x8, "object": 0x1}
LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB, LC_RPATH = 0xC, 0xD, 0x80000018, 0x8000001C


def _pad(b: bytes) -> bytes:
    b += b"\0"
    return b + b"\0" * (-len(b) % 8)


def macho_slice(
    *,
    filetype: str = "execute",
    install_id: str | None = None,
    loads: Sequence[str] = (),
    weak: Sequence[str] = (),
    rpaths: Sequence[str] = (),
    little: bool = True,
) -> bytes:
    e = "<" if little else ">"
    cmds: list[bytes] = []

    def dylib(cmd: int, name: str) -> bytes:
        s = _pad(name.encode())
        return struct.pack(e + "IIIIII", cmd, 24 + len(s), 24, 2, 0x10000, 0x10000) + s

    if install_id is not None:
        cmds.append(dylib(LC_ID_DYLIB, install_id))
    cmds += [dylib(LC_LOAD_DYLIB, n) for n in loads]
    cmds += [dylib(LC_LOAD_WEAK_DYLIB, n) for n in weak]
    for r in rpaths:
        s = _pad(r.encode())
        cmds.append(struct.pack(e + "III", LC_RPATH, 12 + len(s), 12) + s)
    blob = b"".join(cmds)
    hdr = struct.pack(
        e + "IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, _MH[filetype], len(cmds), len(blob), 0, 0
    )
    return hdr + blob


def macho(path: Path, *, fat: Sequence[bytes] | None = None, **kw) -> Path:
    """Write a 64-bit Mach-O (``macho_slice`` kwargs), or a universal one of *fat*."""
    if fat is None:
        data = macho_slice(**kw)
    else:
        offs, out = [], bytearray(struct.pack(">II", 0xCAFEBABE, len(fat)))
        out += b"\0" * (20 * len(fat))
        for sl in fat:
            out += b"\0" * (-len(out) % 4096)
            offs.append(len(out))
            out += sl
        for i, (sl, off) in enumerate(zip(fat, offs)):
            struct.pack_into(">iiIII", out, 8 + 20 * i, 0x0100000C, 0, off, len(sl), 12)
        data = bytes(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
