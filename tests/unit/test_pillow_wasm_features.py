"""recipes/pillow-cp312/wasm_features.py reads the target_features of the archives PIL links.

build-wasm.sh links each wasm extension relocatable (`wasm-ld -r`), which keeps only the '+'
entries of its inputs' target_features: a member's '-shared-mem' never reaches the extension's
object, so the probe's -pthread link can't refuse it. The script reads the codec archives
themselves; on wasm-mt (--shared-memory) such a member fails the build. These tests build the
objects and archives byte by byte, so they need no wasm toolchain.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "recipes" / "pillow-cp312" / "wasm_features.py"
_spec = importlib.util.spec_from_file_location("pillow_wasm_features", TOOL)
wf = importlib.util.module_from_spec(_spec)
sys.modules["pillow_wasm_features"] = wf
_spec.loader.exec_module(wf)


def _enc(v: int) -> bytes:
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        out.append(byte | (0x80 if v else 0))
        if not v:
            return bytes(out)


def _str(s: str) -> bytes:
    raw = s.encode()
    return _enc(len(raw)) + raw


def _section(sid: int, body: bytes) -> bytes:
    return bytes([sid]) + _enc(len(body)) + body


def obj(*features: str) -> bytes:
    """A wasm object: a type section, an unrelated custom section, then target_features (none
    when no features are given)."""
    out = b"\0asm\x01\0\0\0" + _section(1, b"\x01\x60\x00\x00")
    out += _section(0, _str("producers") + b"\x00")
    if features:
        payload = _enc(len(features)) + b"".join(f[0].encode() + _str(f[1:]) for f in features)
        out += _section(0, _str("target_features") + payload)
    return out


def _hdr(name: bytes, size: int) -> bytes:
    return (
        name.ljust(16)
        + b"0".ljust(12)
        + b"0".ljust(6)
        + b"0".ljust(6)
        + b"644".ljust(8)
        + str(size).encode().ljust(10)
        + b"`\n"
    )


def gnu_archive(members: dict) -> bytes:
    """A GNU ar archive (llvm-ar's default), with a symbol index and a long-name table."""
    out = b"!<arch>\n"
    index = b"\0\0\0\0"
    out += _hdr(b"/", len(index)) + index
    longnames, names = b"", {}
    for name in members:
        if len(name) > 15:
            names[name] = b"/%d" % len(longnames)
            longnames += name.encode() + b"/\n"
        else:
            names[name] = name.encode() + b"/"
    if longnames:
        out += _hdr(b"//", len(longnames)) + longnames + (b"\n" if len(longnames) & 1 else b"")
    for name, body in members.items():
        out += _hdr(names[name], len(body)) + body + (b"\n" if len(body) & 1 else b"")
    return out


def bsd_archive(members: dict) -> bytes:
    """A BSD ar archive: each name leads its member's body (#1/<length>)."""
    out = b"!<arch>\n"
    for name, body in members.items():
        raw = name.encode()
        data = raw + body
        out += _hdr(b"#1/%d" % len(raw), len(data)) + data + (b"\n" if len(data) & 1 else b"")
    return out


def test_reads_the_target_features_section():
    assert wf.target_features(obj("+atomics", "+bulk-memory", "-shared-mem")) == [
        "+atomics",
        "+bulk-memory",
        "-shared-mem",
    ]
    assert wf.target_features(obj()) == []  # no section: no features
    assert wf.target_features(b"BC\xc0\xde....") is None  # bitcode isn't a wasm object


def test_a_malformed_section_is_an_error():
    good = obj("+atomics")
    with pytest.raises(wf.FeatureError):
        wf.target_features(good[:-3])  # truncated
    with pytest.raises(wf.FeatureError):
        wf.target_features(b"\0asm\x02\0\0\0")  # another binary version
    bad_prefix = obj("+atomics").replace(b"+" + _str("atomics"), b"?" + _str("atomics"))
    with pytest.raises(wf.FeatureError, match="prefix"):
        wf.target_features(bad_prefix)


@pytest.mark.parametrize("make", [gnu_archive, bsd_archive])
def test_survey_counts_each_member(tmp_path, make):
    a = tmp_path / "libtiff.a"
    members = {
        "tif_aux.c.o": obj("+atomics", "+bulk-memory"),
        "tif_jpeg.c.o": obj("+bulk-memory", "-shared-mem"),
        "a_long_member_name_for_the_table.c.o": obj("+atomics"),
        "README": b"not an object\n",
    }
    assert any(len(b) & 1 for b in members.values())  # a padded member is read past
    a.write_bytes(make(members))
    s = wf.survey(a)
    assert s == {
        "wasm": 3,
        "atomics": 2,
        "no_shared_mem": ["tif_jpeg.c.o"],
        "other": ["README"],
    }


def test_archives_for_resolves_libs_in_order(tmp_path):
    for n in ("tiff", "z", "webp"):
        (tmp_path / f"lib{n}.a").write_bytes(gnu_archive({"x.o": obj()}))
    found, skipped = wf.archives_for(tmp_path, "-ltiff -lm -lz -lwebp -lz -pthread")
    assert [p.name for p in found] == ["libtiff.a", "libz.a", "libwebp.a"]
    assert skipped == ["-lm"]


def _prefix(tmp_path, lowered: bool) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "libwebp.a").write_bytes(gnu_archive({"dec.c.o": obj("+atomics", "+bulk-memory")}))
    feats = ("+bulk-memory", "-shared-mem") if lowered else ("+bulk-memory",)
    (lib / "libbz2.a").write_bytes(gnu_archive({"bzlib.c.o": obj(*feats)}))
    return lib


def test_shared_memory_refuses_a_lowered_member(tmp_path, capsys):
    lib = _prefix(tmp_path, lowered=True)
    rc = wf.main(["--shared-memory", "--libdir", str(lib), "--libs=-lwebp -lbz2 -lm"])
    out = capsys.readouterr()
    assert rc == 1
    assert "libbz2.a(bzlib.c.o): -shared-mem" in out.err
    assert "-lm: no archive" in out.out


def test_plain_wasm_only_records(tmp_path, capsys):
    lib = _prefix(tmp_path, lowered=True)
    assert wf.main(["--libdir", str(lib), "--libs=-lwebp -lbz2"]) == 0
    out = capsys.readouterr().out
    assert "libwebp.a: 1 wasm members, +atomics 1, -shared-mem 0" in out
    assert "libbz2.a: 1 wasm members, +atomics 0, -shared-mem 1" in out
    assert "-shared-mem: bzlib.c.o" in out


def test_shared_memory_takes_a_member_built_without_atomics(tmp_path):
    # Built without -pthread but with nothing lowered (wasm-mt's libbz2): wasm-ld takes it.
    lib = _prefix(tmp_path, lowered=False)
    assert wf.main(["--shared-memory", "--libdir", str(lib), "--libs=-lwebp -lbz2"]) == 0


def test_shared_memory_refuses_what_it_cant_read(tmp_path, capsys):
    a = tmp_path / "libodd.a"
    a.write_bytes(gnu_archive({"lto.o": b"BC\xc0\xde" + b"\0" * 8}))
    assert wf.main(["--shared-memory", str(a)]) == 1
    assert "not a wasm object" in capsys.readouterr().err
    assert wf.main([str(a)]) == 0


def test_a_thin_archive_is_an_error(tmp_path, capsys):
    a = tmp_path / "libthin.a"
    a.write_bytes(b"!<thin>\n")
    assert wf.main([str(a)]) == 1
    assert "thin archive" in capsys.readouterr().err
