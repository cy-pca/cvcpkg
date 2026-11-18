# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Which objects in a wasm install tree use Emscripten's JS exceptions or JS longjmp.

Every wasm and wasm-mt package is built with ``-fwasm-exceptions``: wasm's own
exception handling, and wasm's setjmp/longjmp. ``recipes/_common/env-wasm.sh``
(and ``env-wasm.ps1``) pass it through ``EMCC_CFLAGS``. An object compiled the
old way, with Emscripten's JS exception handling or JS longjmp, can't link into
a program built like that, and the reverse fails too. Linked with emsdk 5.0.7:
- a wasm-exceptions program with a JS-longjmp object: ``emscripten_longjmp``
  is undefined;
- the same with a JS-exceptions object: ``__resumeException`` is undefined;
- a JS-exceptions program with a wasm-longjmp object: ``__wasm_longjmp`` is
  undefined.

So one stale package breaks every consumer's link (libcvc
``docs/roadmap/WASM_JSPI_EXCEPTIONS.md``, section 2).

A JS-exception or JS-longjmp object is easy to recognise. It imports the
functions that implement those modes in JS (see :data:`JS_EH_IMPORT`):
- ``invoke_<sig>``, the trampolines every guarded call goes through;
- ``__resumeException`` and ``__cxa_find_matching_catch_<n>`` (JS exceptions);
- ``emscripten_longjmp`` (JS longjmp).

In a relocatable wasm object every undefined function is an import, so the
import section is all that has to be read. This module reads it directly from
``ar`` archives and ``.o`` files, with no llvm-nm, so a pack checks the tree on
any host.

An LTO member (``-flto``) is LLVM bitcode, and its exceptions and longjmp are
lowered only at the consumer's link. setjmp is then lowered the consumer's way.
Exceptions are not: clang has already shaped the IR for one mode. JS exception
IR uses the personality ``__gxx_personality_v0``, wasm exception IR uses
``__gxx_wasm_personality_v0``, and the name is in the bitcode's string table.
mujoco ships bitcode with the former.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

#: A function import that only an object built with Emscripten's JS exception
#: handling or its JS setjmp/longjmp has.
JS_EH_IMPORT = re.compile(
    r"^(?:invoke_[a-zA-Z0-9]+|__resumeException|__cxa_find_matching_catch_\d+|emscripten_longjmp)$"
)

_WASM_MAGIC = b"\0asm"
_BITCODE_MAGIC = b"BC\xc0\xde"
#: The C++ personality of JS exception IR. Wasm exception IR uses
#: ``__gxx_wasm_personality_v0``, which doesn't contain this string.
_JS_EH_PERSONALITY = b"__gxx_personality_v0"
_AR_MAGIC = b"!<arch>\n"
_AR_THIN_MAGIC = b"!<thin>\n"


class WasmParseError(ValueError):
    """An archive or object that isn't what its magic says."""


def _leb(data: bytes, pos: int) -> tuple[int, int]:
    """Decode an unsigned LEB128 at *pos*; return ``(value, next position)``."""
    result = shift = 0
    while True:
        if pos >= len(data):
            raise WasmParseError("truncated LEB128")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _name(data: bytes, pos: int) -> tuple[str, int]:
    n, pos = _leb(data, pos)
    if pos + n > len(data):
        raise WasmParseError("truncated name")
    return data[pos : pos + n].decode("utf-8", "replace"), pos + n


def _limits(data: bytes, pos: int) -> int:
    flags, pos = _leb(data, pos)
    _, pos = _leb(data, pos)
    if flags & 0x1:
        _, pos = _leb(data, pos)
    return pos


def function_imports(obj: bytes) -> list[str]:
    """The names of the functions a wasm object imports, in order.

    Only the import section is read. A module without one imports nothing.
    """
    if obj[:4] != _WASM_MAGIC:
        raise WasmParseError("not a wasm object")
    pos = 8
    while pos < len(obj):
        section, pos = obj[pos], pos + 1
        size, pos = _leb(obj, pos)
        end = pos + size
        if end > len(obj):
            raise WasmParseError("truncated section")
        if section == 2:
            return _imports_in(obj[pos:end])
        if section not in (0, 1):
            # Sections come in order: type (1), then import (2). Past them
            # there is none to find.
            return []
        pos = end
    return []


def _imports_in(sec: bytes) -> list[str]:
    names: list[str] = []
    count, pos = _leb(sec, 0)
    for _ in range(count):
        _module, pos = _name(sec, pos)
        field, pos = _name(sec, pos)
        kind, pos = sec[pos], pos + 1
        if kind == 0:  # function: a type index
            _, pos = _leb(sec, pos)
            names.append(field)
        elif kind == 1:  # table: a reference type, then limits
            pos = _limits(sec, pos + 1)
        elif kind == 2:  # memory: limits
            pos = _limits(sec, pos)
        elif kind == 3:  # global: a value type and mutability
            pos += 2
        elif kind == 4:  # tag: an attribute byte, then a type index
            _, pos = _leb(sec, pos + 1)
        else:
            raise WasmParseError(f"unknown import kind {kind}")
    return names


def archive_members(data: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, bytes)`` for each member of a GNU or BSD ``ar`` archive.

    The symbol tables (``/``, ``/SYM64/``, ``__.SYMDEF*``) and the GNU long-name
    table are not members and are skipped.
    """
    if data[:8] == _AR_THIN_MAGIC:
        raise WasmParseError("a thin archive (its members live elsewhere)")
    if data[:8] != _AR_MAGIC:
        raise WasmParseError("not an ar archive")
    pos, long_names = 8, b""
    while pos + 60 <= len(data):
        header = data[pos : pos + 60]
        if header[58:60] != b"`\n":
            raise WasmParseError(f"bad member header at {pos}")
        raw = header[:16].decode("ascii", "replace").rstrip()
        size = int(header[48:58].decode("ascii").strip() or "0")
        body = data[pos + 60 : pos + 60 + size]
        pos += 60 + size + (size & 1)
        if raw in ("/", "/SYM64/") or raw.startswith("__.SYMDEF"):
            continue
        if raw == "//":
            long_names = body
            continue
        if raw.startswith("#1/"):  # BSD: the name is the body's first n bytes
            n = int(raw[3:])
            name, body = body[:n].rstrip(b"\0").decode("utf-8", "replace"), body[n:]
            if name.startswith("__.SYMDEF"):
                continue
        elif raw.startswith("/") and raw[1:].isdigit():  # GNU: an offset into "//"
            off = int(raw[1:])
            name = long_names[off : long_names.index(b"\n", off)].decode("utf-8", "replace")
            name = name.rstrip("/")
        else:
            name = raw.rstrip("/")
        yield name, body


def _member_hits(body: bytes) -> set[str]:
    if body[:4] == _WASM_MAGIC:
        return {s for s in function_imports(body) if JS_EH_IMPORT.match(s)}
    if body[:4] == _BITCODE_MAGIC and _JS_EH_PERSONALITY in body:
        return {_JS_EH_PERSONALITY.decode() + " (LTO bitcode)"}
    return set()


def js_eh_members(path: Path) -> dict[str, set[str]]:
    """The members of an archive or object that use JS exceptions or JS longjmp.

    Returns ``{member: {the imports, or the bitcode's personality, that show
    it}}``. Members that are neither wasm nor bitcode are ignored.
    """
    data = path.read_bytes()
    if data[:4] in (_WASM_MAGIC, _BITCODE_MAGIC):
        members: Iterator[tuple[str, bytes]] = iter([(path.name, data)])
    else:
        members = archive_members(data)
    found: dict[str, set[str]] = {}
    for name, body in members:
        hits = _member_hits(body)
        if hits:
            found[name] = hits
    return found


def check_wasm_exceptions(install_dir: Path) -> tuple[list[str], list[str]]:
    """Check every static archive and object under *install_dir*.

    Returns ``(problems, notes)``: one problem line per archive or object that
    uses JS exceptions or JS longjmp, and a note for each archive that couldn't
    be read (a thin archive, a malformed one). Symlinks are not followed, so an
    archive linked under a second name is checked once.
    """
    problems: list[str] = []
    notes: list[str] = []
    for root, _dirs, files in os.walk(install_dir):
        for f in sorted(files):
            if not f.endswith((".a", ".o")):
                continue
            path = Path(root) / f
            if path.is_symlink():
                continue
            rel = path.relative_to(install_dir).as_posix()
            try:
                found = js_eh_members(path)
            except WasmParseError as exc:
                if f.endswith(".a"):
                    notes.append(f"{rel}: not checked ({exc})")
                continue
            if not found:
                continue
            members = sorted(found)
            symbols = sorted(set().union(*found.values()))
            shown = ", ".join(members[:3]) + (", ..." if len(members) > 3 else "")
            sym = ", ".join(symbols[:5]) + (", ..." if len(symbols) > 5 else "")
            problems.append(f"{rel}: {len(members)} member(s) ({shown}) use {sym}")
    problems.sort()
    notes.sort()
    return problems, notes
