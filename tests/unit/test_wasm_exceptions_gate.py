"""A wasm bundle must not carry objects built with JS exceptions or JS longjmp.

Every wasm and wasm-mt package is built with -fwasm-exceptions (EMCC_CFLAGS in
_common/env-wasm.sh). emsdk 5.0.7 won't link a JS-exceptions or JS-longjmp object
into such a program: `emscripten_longjmp` or `__resumeException` is undefined.
The reverse fails as well (`__wasm_longjmp`). Packing therefore refuses an
archive whose members import the JS side of those modes. The objects below are
built by hand, so the tests need no emsdk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cvcpkg.builder import BuildError, enforce_wasm_exceptions, pack_from_prefix
from cvcpkg.wasm_eh import (
    WasmParseError,
    archive_members,
    check_wasm_exceptions,
    function_imports,
    js_eh_members,
)


def _leb(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode()
    return _leb(len(b)) + b


def _section(sid: int, body: bytes) -> bytes:
    return bytes([sid]) + _leb(len(body)) + body


def wasm_object(*functions: str, other_imports: bool = True) -> bytes:
    """A relocatable wasm object importing *functions* (and, by default, one
    import of each other kind, as real objects have)."""
    types = _section(1, _leb(1) + b"\x60\x00\x00")
    entries = [_str("env") + _str(f) + b"\x00" + _leb(0) for f in functions]
    if other_imports:
        entries = [
            _str("env") + _str("__linear_memory") + b"\x02\x00" + _leb(1),
            _str("env") + _str("__indirect_function_table") + b"\x01\x70\x01" + _leb(0) + _leb(9),
            _str("env") + _str("__stack_pointer") + b"\x03\x7f\x01",
            _str("env") + _str("__cpp_exception") + b"\x04\x00" + _leb(0),
            *entries,
        ]
    imports = _section(2, _leb(len(entries)) + b"".join(entries))
    code = _section(10, _leb(0))
    custom = _section(0, _str("linking") + b"\x02")
    return b"\0asm\x01\0\0\0" + types + imports + code + custom


def _ar_header(name: str, size: int) -> bytes:
    h = f"{name:<16}{0:<12}{0:<6}{0:<6}{644:<8}{size:<10}`\n".encode()
    assert len(h) == 60
    return h


def gnu_archive(members: dict[str, bytes]) -> bytes:
    """A GNU ar archive with a symbol table and a long-name table, as emar writes."""
    out = bytearray(b"!<arch>\n")
    symtab = b"\0\0\0\0"
    out += _ar_header("/", len(symtab)) + symtab
    longs = bytearray()
    names = {}
    for name in members:
        if len(name) > 15:
            names[name] = f"/{len(longs)}"
            longs += name.encode() + b"/\n"
        else:
            names[name] = name + "/"
    if longs:
        out += _ar_header("//", len(longs)) + longs + (b"\n" if len(longs) & 1 else b"")
    for name, body in members.items():
        out += _ar_header(names[name], len(body)) + body + (b"\n" if len(body) & 1 else b"")
    return bytes(out)


def bsd_archive(members: dict[str, bytes]) -> bytes:
    """A BSD ar archive (``#1/<n>`` names), as llvm-ar writes on macOS."""
    out = bytearray(b"!<arch>\n")
    for name, body in {"__.SYMDEF SORTED": b"\0" * 8, **members}.items():
        nb = name.encode()
        data = nb + body
        out += _ar_header(f"#1/{len(nb)}", len(data)) + data + (b"\n" if len(data) & 1 else b"")
    return bytes(out)


JS_SJLJ = wasm_object("png_error", "emscripten_longjmp", "invoke_vii")
JS_EH = wasm_object("__cxa_throw", "__resumeException", "__cxa_find_matching_catch_2", "invoke_ii")
CLEAN = wasm_object("__cxa_throw", "__wasm_longjmp", "__wasm_setjmp", "memcpy")
# LTO members: bitcode whose string table names the C++ personality its IR was shaped for.
BITCODE_JS_EH = b"BC\xc0\xde" + b"\0" * 16 + b"__cxa_throw__gxx_personality_v0setjmp"
BITCODE_WASM_EH = b"BC\xc0\xde" + b"\0" * 16 + b"__cxa_throw__gxx_wasm_personality_v0setjmp"


def test_function_imports_skip_the_other_kinds():
    assert function_imports(JS_SJLJ) == ["png_error", "emscripten_longjmp", "invoke_vii"]
    assert function_imports(wasm_object(other_imports=False)) == []


def test_function_imports_rejects_a_non_wasm_body():
    with pytest.raises(WasmParseError):
        function_imports(b"\x7fELF" + b"\0" * 60)


def test_gnu_archive_members_resolve_long_names_and_skip_tables():
    data = gnu_archive({"a.o": CLEAN, "a_rather_long_member_name.c.o": JS_SJLJ, "odd.o": b"x"})
    assert [n for n, _ in archive_members(data)] == [
        "a.o",
        "a_rather_long_member_name.c.o",
        "odd.o",
    ]


def test_bsd_archive_members_take_their_names_from_the_body():
    data = bsd_archive({"png.c.o": JS_SJLJ, "pngread.c.o": CLEAN})
    assert [(n, b[:4]) for n, b in archive_members(data)] == [
        ("png.c.o", b"\0asm"),
        ("pngread.c.o", b"\0asm"),
    ]


@pytest.mark.parametrize("build", [gnu_archive, bsd_archive])
def test_js_eh_members_names_the_offending_members_and_imports(tmp_path, build):
    lib = tmp_path / "libx.a"
    lib.write_bytes(build({"png.c.o": JS_SJLJ, "thr.cpp.o": JS_EH, "ok.c.o": CLEAN}))
    assert js_eh_members(lib) == {
        "png.c.o": {"emscripten_longjmp", "invoke_vii"},
        "thr.cpp.o": {"__resumeException", "__cxa_find_matching_catch_2", "invoke_ii"},
    }


def test_a_wasm_exceptions_object_passes(tmp_path):
    obj = tmp_path / "ok.o"
    obj.write_bytes(CLEAN)
    assert js_eh_members(obj) == {}


def test_lto_bitcode_is_judged_by_its_personality(tmp_path):
    lib = tmp_path / "libmujoco.a"
    lib.write_bytes(gnu_archive({"engine.c.o": BITCODE_JS_EH, "plugin.cc.o": BITCODE_WASM_EH}))
    assert js_eh_members(lib) == {"engine.c.o": {"__gxx_personality_v0 (LTO bitcode)"}}


def test_check_reports_each_archive_once_and_notes_what_it_cannot_read(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libpng16.a").write_bytes(gnu_archive({"png.c.o": JS_SJLJ}))
    (tmp_path / "lib" / "libz.a").write_bytes(gnu_archive({"adler32.c.o": CLEAN}))
    (tmp_path / "lib" / "liblto.a").write_bytes(gnu_archive({"x.o": BITCODE_WASM_EH}))
    (tmp_path / "lib" / "libthin.a").write_bytes(b"!<thin>\n")
    (tmp_path / "lib" / "python.wasm").write_bytes(JS_EH)  # a linked module, not an object
    problems, notes = check_wasm_exceptions(tmp_path)
    assert problems == ["lib/libpng16.a: 1 member(s) (png.c.o) use emscripten_longjmp, invoke_vii"]
    assert notes == ["lib/libthin.a: not checked (a thin archive (its members live elsewhere))"]


def test_only_wasm_packs_are_checked(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libpng16.a").write_bytes(gnu_archive({"png.c.o": JS_SJLJ}))
    enforce_wasm_exceptions(tmp_path, "linux", "libpng")
    enforce_wasm_exceptions(tmp_path, "wasi", "libpng")
    for platform in ("wasm", "wasm-mt"):
        with pytest.raises(BuildError, match=r"libpng16\.a: 1 member\(s\) \(png\.c\.o\)"):
            enforce_wasm_exceptions(tmp_path, platform, "libpng")


def _wasm_recipe(tmp_path: Path) -> Path:
    d = tmp_path / "recipes" / "libpng"
    d.mkdir(parents=True)
    recipe = {
        "schema_version": 1,
        "recipe": {"name": "libpng", "upstream_version": "1.6.44", "cvc_revision": 7},
        "source": {"type": "vendored", "path": "third-party/x"},
        "patches": [],
        "build": {"matrix": [{"platform": "wasm", "script": "build-wasm.sh"}]},
        "package": {"files": ["lib/libpng16.a"]},
    }
    (d / "recipe.yaml").write_text(yaml.dump(recipe), encoding="utf-8")
    return d


def test_pack_from_prefix_refuses_a_js_longjmp_archive(tmp_path):
    prefix = tmp_path / "prefix"
    (prefix / "lib").mkdir(parents=True)
    (prefix / "lib" / "libpng16.a").write_bytes(gnu_archive({"png.c.o": JS_SJLJ}))
    with pytest.raises(BuildError, match=r"JS setjmp/longjmp"):
        pack_from_prefix(
            _wasm_recipe(tmp_path),
            prefix,
            platform="wasm",
            arch="wasm32",
            output_dir=tmp_path / "dist",
        )
    assert not (tmp_path / "dist").exists()


def test_pack_from_prefix_packs_a_wasm_exceptions_archive(tmp_path):
    prefix = tmp_path / "prefix"
    (prefix / "lib").mkdir(parents=True)
    (prefix / "lib" / "libpng16.a").write_bytes(gnu_archive({"png.c.o": CLEAN}))
    archive, _, _ = pack_from_prefix(
        _wasm_recipe(tmp_path), prefix, platform="wasm", arch="wasm32", output_dir=tmp_path / "dist"
    )
    assert archive.is_file()
