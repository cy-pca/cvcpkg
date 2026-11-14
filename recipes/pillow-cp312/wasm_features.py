#!/usr/bin/env python3
"""recipes/pillow-cp312/wasm_features.py: the target_features of the archives an extension links.

build-wasm.sh links each extension closed: `emcc -shared` (no SIDE_MODULE) is
`wasm-ld --relocatable`, which merges the archive members an extension uses into its object. The
merged object keeps only the '+' (used) entries of its inputs' target_features sections, so a
member's '-shared-mem' is dropped. clang writes that marker on code built without atomics whose
atomics or thread-locals it lowered to plain ones, and wasm-ld refuses such a member in a
--shared-memory (-pthread) link. Once it is merged into an extension, that link can't see it, and
the host's threads would share the member's thread-locals and race on its atomics. So
build-wasm.sh has this read the archives themselves, before the probe.

usage: wasm_features.py [--shared-memory] [--libdir DIR --libs=-lA -lB ...] [ARCHIVE ...]

--libs takes pkg-config's --libs-only-l output: each -lNAME is DIR/libNAME.a, and a name with no
archive in DIR comes from the Emscripten sysroot (-lm) and is skipped. For each archive it prints
how many members are wasm objects, how many carry '+atomics' and which carry '-shared-mem'. With
--shared-memory (the wasm-mt flavor) a member that carries '-shared-mem', or that isn't a wasm
object it can read, is an error (exit 1). Without it (plain wasm) that is only recorded: a module
without shared memory takes both kinds of member. Stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DISALLOWED, USED, REQUIRED = 0x2D, 0x2B, 0x3D  # '-', '+', '=' (tool-conventions, Linking.md)


class FeatureError(Exception):
    pass


def _uleb(buf: bytes, i: int) -> tuple:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise FeatureError("truncated LEB128")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return result, i
        if shift > 63:
            raise FeatureError("LEB128 too long")


def target_features(data: bytes):
    """The target_features entries of a wasm object ('+atomics', '-shared-mem', ...), or None
    when data isn't a wasm object. An object without the section has none."""
    if data[:4] != b"\0asm":
        return None
    if data[4:8] != b"\x01\x00\x00\x00":
        raise FeatureError("unsupported WebAssembly binary version")
    feats = []
    i = 8
    while i < len(data):
        sid = data[i]
        size, j = _uleb(data, i + 1)
        end = j + size
        if end > len(data):
            raise FeatureError("truncated section")
        if sid == 0:
            n, k = _uleb(data, j)
            if data[k : k + n] == b"target_features":
                k += n
                count, k = _uleb(data, k)
                for _ in range(count):
                    if k >= end:
                        raise FeatureError("truncated target_features")
                    prefix = data[k]
                    if prefix not in (DISALLOWED, USED, REQUIRED):
                        raise FeatureError(f"bad target_features prefix 0x{prefix:02x}")
                    n, k = _uleb(data, k + 1)
                    if k + n > end:
                        raise FeatureError("truncated target_features")
                    feats.append(chr(prefix) + data[k : k + n].decode("utf-8", "replace"))
                    k += n
        i = end
    return feats


def members(path: Path):
    """(name, bytes) for each member of a static archive (GNU or BSD ar), or the file itself
    when it isn't one."""
    data = path.read_bytes()
    if data[:8] == b"!<thin>\n":
        raise FeatureError(f"{path}: a thin archive (its members live elsewhere)")
    if data[:8] != b"!<arch>\n":
        yield path.name, data
        return
    longnames = b""
    i = 8
    while i < len(data):
        if data[i : i + 1] == b"\n":  # padding between members
            i += 1
            continue
        hdr = data[i : i + 60]
        if len(hdr) < 60 or hdr[58:60] != b"`\n":
            raise FeatureError(f"{path}: bad member header at offset {i}")
        name = hdr[:16].rstrip(b" ")
        try:
            size = int(hdr[48:58].strip() or b"0")
        except ValueError as e:
            raise FeatureError(f"{path}: bad member size at offset {i}") from e
        body = data[i + 60 : i + 60 + size]
        if len(body) != size:
            raise FeatureError(f"{path}: truncated member at offset {i}")
        i += 60 + size + (size & 1)
        if name in (b"/", b"/SYM64/", b"__.SYMDEF", b"__.SYMDEF SORTED"):
            continue  # the symbol index
        if name == b"//":
            longnames = body
            continue
        if name.startswith(b"#1/"):  # BSD: the name leads the body
            n = int(name[3:])
            name, body = body[:n].rstrip(b"\0"), body[n:]
            if name.startswith(b"__.SYMDEF"):
                continue
        elif name.startswith(b"/") and name[1:].isdigit():  # GNU: an offset into //
            off = int(name[1:])
            end = longnames.find(b"\n", off)
            name = longnames[off : end if end >= 0 else len(longnames)].rstrip(b"/")
        else:
            name = name.rstrip(b"/")
        yield name.decode("utf-8", "replace"), body


def archives_for(libdir: Path, libs: str) -> tuple:
    """(archives, skipped -l names) for pkg-config's --libs-only-l output, in its order, each
    archive once."""
    found, skipped = [], []
    for tok in libs.split():
        if not tok.startswith("-l"):
            continue
        a = libdir / f"lib{tok[2:]}.a"
        if a.is_file():
            if a not in found:
                found.append(a)
        elif tok not in skipped:
            skipped.append(tok)
    return found, skipped


def survey(path: Path) -> dict:
    """What an archive's members carry: counts, and the names of the '-shared-mem' members and
    of those that aren't wasm objects."""
    out = {"wasm": 0, "atomics": 0, "no_shared_mem": [], "other": []}
    for name, body in members(path):
        feats = target_features(body)
        if feats is None:
            out["other"].append(name)
            continue
        out["wasm"] += 1
        if "+atomics" in feats:
            out["atomics"] += 1
        if "-shared-mem" in feats:
            out["no_shared_mem"].append(name)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--shared-memory",
        action="store_true",
        help="the archives go into a shared-memory (-pthread) module: fail on a member that "
        "disallows it",
    )
    ap.add_argument("--libdir", type=Path, help="where -lNAME's libNAME.a is")
    ap.add_argument("--libs", default="", help="pkg-config --libs-only-l output (pass as --libs=)")
    ap.add_argument("archives", nargs="*", type=Path)
    args = ap.parse_args(argv)
    if args.libs and not args.libdir:
        ap.error("--libs needs --libdir")
    paths = list(args.archives)
    if args.libs:
        found, skipped = archives_for(args.libdir, args.libs)
        paths += [p for p in found if p not in paths]
        for tok in skipped:
            print(f"wasm_features: {tok}: no archive in {args.libdir} (the sysroot's), not read")
    if not paths:
        ap.error("no archives to read")
    flavor = "shared memory" if args.shared_memory else "no shared memory"
    bad = []
    for p in paths:
        try:
            s = survey(p)
        except (OSError, FeatureError) as e:
            sys.stdout.flush()
            print(f"wasm_features: FATAL: {p}: {e}", file=sys.stderr)
            return 1
        line = (
            f"wasm_features: {p.name}: {s['wasm']} wasm members, +atomics {s['atomics']}, "
            f"-shared-mem {len(s['no_shared_mem'])}"
        )
        if s["other"]:
            line += f", {len(s['other'])} not wasm"
        print(line)
        for what, names in (("-shared-mem", s["no_shared_mem"]), ("not wasm", s["other"])):
            if names:
                print(f"    {what}: {' '.join(names)}")
        if args.shared_memory:
            bad += [f"{p.name}({n}): -shared-mem" for n in s["no_shared_mem"]]
            bad += [f"{p.name}({n}): not a wasm object, its features unread" for n in s["other"]]
    if bad:
        sys.stdout.flush()  # the survey first, then why it failed, when both go to one log
        print(
            f"wasm_features: FATAL: {len(bad)} member(s) can't go into a module with shared "
            "memory (rebuild the library with -pthread):",
            file=sys.stderr,
        )
        for b in bad:
            print(f"    {b}", file=sys.stderr)
        return 1
    print(f"wasm_features: {len(paths)} archive(s) read ({flavor})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
