"""recipes/_common/wasm_pyext.py gives each wasm Python extension a namespace of its own.

Python C extensions linked statically into one wasm module share one namespace: `PyInit_<leaf>`
collides across packages, and copies of a helper library collide too. The tool rewrites each
extension object's `linking` symbol table (PyInit renamed after the dotted module, every other
defined global made local, COMDATs prefixed) and lists the extensions in an inittab manifest.

Most tests build relocatable objects byte by byte, so they need no wasm toolchain. The last one
compiles and links real objects with emsdk's clang and wasm-ld when CVC_EMSDK_DIR points at one.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "recipes" / "_common" / "wasm_pyext.py"
_spec = importlib.util.spec_from_file_location("wasm_pyext", TOOL)
wp = importlib.util.module_from_spec(_spec)
sys.modules["wasm_pyext"] = wp  # its dataclasses look their module up there
_spec.loader.exec_module(wp)

FUNC, DATA, GLOBAL, SECTION = 0, 1, 2, 3
WEAK, LOCAL, UNDEF, EXPORTED, EXPLICIT, NO_STRIP, TLS = 0x1, 0x2, 0x10, 0x20, 0x40, 0x80, 0x100


def _enc(v: int) -> bytes:
    return wp._enc(v)


def _str(s: str) -> bytes:
    raw = s.encode()
    return _enc(len(raw)) + raw


def _section(sid: int, body: bytes) -> bytes:
    return bytes([sid]) + _enc(len(body)) + body


def _custom(name: str, payload: bytes) -> bytes:
    return _section(0, _str(name) + payload)


def _sub(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + _enc(len(payload)) + payload


def _symtab(entries) -> bytes:
    out = _enc(len(entries))
    for kind, flags, index, name, tail in entries:
        out += bytes([kind]) + _enc(flags)
        if kind in (FUNC, GLOBAL):
            out += _enc(index)
            if name is not None:
                out += _str(name)
        elif kind == DATA:
            out += _str(name)
            if tail is not None:
                out += b"".join(_enc(v) for v in tail)
        else:
            out += _enc(index)
    return out


SEGMENT_INFO = _sub(5, _enc(1) + _str(".data.x") + _enc(2) + _enc(0))


def make_object(leaf: str = "foo", extra=(), dylink: bool = False, version: int = 2) -> bytes:
    """A relocatable object with the symbol kinds an extension has."""
    entries = [
        (FUNC, 0, 0, f"PyInit_{leaf}", None),  # the init function
        (FUNC, 0, 1, "helper", None),  # an internal function
        (FUNC, WEAK, 2, "inline_fn", None),  # a weak (inline) definition
        (FUNC, UNDEF, 3, None, None),  # an import from libpython
        (FUNC, UNDEF | EXPLICIT, 4, "PyModule_Create2", None),
        (DATA, 0, None, "table", (0, 0, 16)),  # defined data
        (DATA, TLS, None, "tls_state", (1, 0, 8)),  # thread-local data
        (DATA, UNDEF, None, "_Py_NoneStruct", None),  # imported data
        (GLOBAL, UNDEF, 0, None, None),  # __stack_pointer
        (SECTION, LOCAL, 2, None, None),
        (FUNC, LOCAL, 5, "already_static", None),
        *extra,
    ]
    comdat = _sub(7, _enc(1) + _str("inline_fn") + _enc(0) + _enc(1) + bytes([0]) + _enc(2))
    linking = _enc(version) + _sub(8, _symtab(entries)) + SEGMENT_INFO + comdat
    names = _sub(0, _str("mod")) + _sub(
        1, _enc(2) + _enc(0) + _str("PyInit_x") + _enc(1) + _str("helper")
    )
    sections = _section(1, b"\x01\x60\x00\x00")  # a type section, passed through untouched
    if dylink:
        sections = _custom("dylink.0", b"\x01\x00") + sections
    return b"\0asm\x01\0\0\0" + sections + _custom("linking", linking) + _custom("name", names)


def _by_name(data: bytes) -> dict:
    return {s.name: s for s in wp.symbols(data) if s.name}


def test_init_symbol_is_named_after_the_dotted_module():
    assert wp.init_symbol_for("numpy.random._common") == "PyInit__x_numpy__random___common"
    assert wp.init_symbol_for("a._common") != wp.init_symbol_for("b._common")
    with pytest.raises(wp.LocalizeError):
        wp.init_symbol_for("numpy..x")


def test_rewrite_renames_init_and_localizes_every_other_definition():
    new, rep = wp.localize_bytes(make_object("_common"), "pkg.sub._common")
    syms = _by_name(new)
    assert "PyInit__common" not in syms
    init = syms["PyInit__x_pkg__sub___common"]
    assert init.defined and not init.local
    for name in ("helper", "inline_fn", "table", "tls_state", "already_static"):
        assert syms[name].local, name
    assert not syms["inline_fn"].flags & WEAK
    assert syms["tls_state"].flags & TLS
    assert not syms["PyModule_Create2"].local and not syms["PyModule_Create2"].defined
    assert not syms["_Py_NoneStruct"].local
    assert wp.defined_globals(new) == ["PyInit__x_pkg__sub___common"]
    assert rep.localized == 4 and rep.comdats == 1 and rep.kept_global == []


def test_symbol_order_and_count_survive():
    old, new = make_object(), wp.localize_bytes(make_object(), "m.foo")[0]
    assert [(s.kind, s.flags & UNDEF) for s in wp.symbols(old)] == [
        (s.kind, s.flags & UNDEF) for s in wp.symbols(new)
    ]


def test_exported_and_no_strip_definitions_stay_global():
    extra = [(FUNC, EXPORTED, 6, "exported_fn", None), (DATA, NO_STRIP, None, "kept", (0, 16, 4))]
    new, rep = wp.localize_bytes(make_object(extra=extra), "m.foo")
    assert sorted(wp.defined_globals(new)) == ["PyInit__x_m__foo", "exported_fn", "kept"]
    assert sorted(rep.kept_global) == ["exported_fn", "kept"]


def test_comdats_and_function_names_get_the_module_prefix():
    new, rep = wp.localize_bytes(make_object(), "m.foo")
    assert b"m.foo::inline_fn" in new and b"m.foo::helper" in new and rep.names == 2
    assert SEGMENT_INFO in new  # other linking subsections are copied as they are
    assert b"\x01\x60\x00\x00" in new  # and so are the other sections


def test_refuses_what_it_cannot_rewrite():
    with pytest.raises(wp.LocalizeError, match="side module"):
        wp.localize_bytes(make_object(dylink=True), "m.foo")
    with pytest.raises(wp.LocalizeError, match="version 1"):
        wp.localize_bytes(make_object(version=1), "m.foo")
    with pytest.raises(wp.LocalizeError, match="PyInit_bar"):
        wp.localize_bytes(make_object("foo"), "m.bar")
    with pytest.raises(wp.LocalizeError, match="relocatable"):
        wp.localize_bytes(b"\0asm\x01\0\0\0" + _section(1, b"\x00"), "m.foo")
    with pytest.raises(wp.LocalizeError, match="not a WebAssembly"):
        wp.localize_bytes(b"\x7fELF\x02\x01\x01\x00", "m.foo")


def test_localizing_twice_changes_nothing(tmp_path):
    src = tmp_path / "foo.so"
    src.write_bytes(make_object())
    wp.localize_object(src, src, "m.foo")
    once = src.read_bytes()
    rep = wp.localize_object(src, src, "m.foo")
    assert rep.already and src.read_bytes() == once


def _site(prefix: Path) -> Path:
    site = prefix / "lib" / "python3.12" / "site-packages"
    tag = ".cpython-312-wasm32-emscripten.so"
    for rel, leaf in [
        ("pkg/_core/_multiarray" + tag, "_multiarray"),
        ("pkg/random/_common" + tag, "_common"),
        ("pkg/_core/_umath_tests" + tag, "_umath_tests"),  # test extensions are left out
        ("pkg/tests/_helper" + tag, "_helper"),
    ]:
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_object(leaf))
    return site


def test_package_localizes_in_place_and_writes_its_manifest(tmp_path):
    _site(tmp_path)
    manifest, reports = wp.localize_package(tmp_path, "pkg", manifest_name="pkg-cp312")
    assert manifest == tmp_path / "share" / "python-wasm" / "pkg-cp312.inittab"
    entries = wp.read_manifest(manifest)
    assert [(d, s) for d, s, _ in entries] == [
        ("pkg._core._multiarray", "PyInit__x_pkg___core___multiarray"),
        ("pkg.random._common", "PyInit__x_pkg__random___common"),
    ]
    for _, symbol, obj in entries:
        assert wp.defined_globals(obj.read_bytes()) == [symbol]
    assert manifest.read_text().startswith("# python-wasm inittab v1")
    assert wp.check([manifest]) == []


def test_package_out_writes_an_overlay_and_leaves_the_prefix_alone(tmp_path):
    prefix, overlay = tmp_path / "deps", tmp_path / "overlay"
    site = _site(prefix)
    before = (site / "pkg/random/_common.cpython-312-wasm32-emscripten.so").read_bytes()
    manifest, _ = wp.localize_package(prefix, "pkg", out=overlay)
    assert (site / "pkg/random/_common.cpython-312-wasm32-emscripten.so").read_bytes() == before
    assert manifest.parent == overlay / "share" / "python-wasm"
    for _, _, obj in wp.read_manifest(manifest):
        assert overlay in obj.parents and obj.is_file()


def test_check_reports_what_would_clash(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _site(a)
    _site(b)
    ma, _ = wp.localize_package(a, "pkg")
    mb, _ = wp.localize_package(b, "pkg", manifest_name="again")
    problems = wp.check([ma, mb])
    assert any("pkg.random._common: listed by" in p for p in problems)

    c = tmp_path / "c"
    (c / "share" / "python-wasm").mkdir(parents=True)
    obj = c / "x.so"
    obj.write_bytes(make_object("x", extra=[(FUNC, EXPORTED, 6, "exported_fn", None)]))
    wp.localize_object(obj, obj, "other.x")
    d = c / "y.so"
    d.write_bytes(make_object("y", extra=[(FUNC, EXPORTED, 6, "exported_fn", None)]))
    wp.localize_object(d, d, "other.y")
    raw = c / "z.so"
    raw.write_bytes(make_object("z"))  # never localized
    mc = c / "share" / "python-wasm" / "other.inittab"
    mc.write_text(
        "other.x\tPyInit__x_other__x\tx.so\n"
        "other.y\tPyInit__x_other__y\ty.so\n"
        "other.z\tPyInit__x_other__z\tz.so\n"
    )
    problems = wp.check([mc])
    assert any(p.startswith("exported_fn: defined by other.x and other.y") for p in problems)
    assert any("other.z" in p and "not localized" in p for p in problems)


def test_manifest_lines_must_match_their_module(tmp_path):
    m = tmp_path / "share" / "python-wasm" / "bad.inittab"
    m.parent.mkdir(parents=True)
    m.write_text("pkg.a\tPyInit_a\tx.so\n")
    with pytest.raises(wp.LocalizeError, match="should map to"):
        wp.read_manifest(m)


def test_registrar_lists_every_extension(tmp_path):
    _site(tmp_path)
    manifest, _ = wp.localize_package(tmp_path, "pkg")
    cpp, objs = tmp_path / "reg.cpp", tmp_path / "objs.txt"
    assert wp.write_registrar([manifest], "register_exts", cpp, objs) == 2
    text = cpp.read_text()
    assert 'extern "C"' in text and "int register_exts(void)" in text
    assert 'PyImport_AppendInittab("pkg.random._common", PyInit__x_pkg__random___common)' in text
    lines = objs.read_text().splitlines()
    assert len(lines) == 2 and all("\\" not in line and Path(line).is_file() for line in lines)


def test_cli_package_and_check(tmp_path, capsys):
    _site(tmp_path)
    assert wp.main(["package", str(tmp_path), "pkg"]) == 0
    assert "pkg.random._common\tPyInit__x_pkg__random___common" in capsys.readouterr().out
    manifest = tmp_path / "share" / "python-wasm" / "pkg.inittab"
    assert wp.main(["check", str(manifest)]) == 0
    elf = tmp_path / "native.so"
    elf.write_bytes(b"\x7fELF\x02\x01\x01\x00")
    assert wp.main(["object", str(elf), str(tmp_path / "o.so"), "m.x"]) == 1
    assert "not a WebAssembly module" in capsys.readouterr().err


# ---- with a real toolchain --------------------------------------------------------------------


def _emsdk_tools():
    root = os.environ.get("CVC_EMSDK_DIR")
    if not root:
        return None
    bin_dir = Path(root) / "upstream" / "bin"
    tools = {}
    for name in ("clang", "wasm-ld"):
        found = shutil.which(name, path=str(bin_dir))
        if not found:
            return None
        tools[name] = found
    return tools


@pytest.mark.skipif(_emsdk_tools() is None, reason="needs CVC_EMSDK_DIR with clang and wasm-ld")
def test_real_extensions_with_one_leaf_name_link_once_localized(tmp_path):
    tools = _emsdk_tools()
    objs = {}
    for pkg, value in (("a", 1), ("b", 2)):
        src = tmp_path / f"{pkg}.c"
        src.write_text(
            "int shared_helper(void) { return %d; }\n"  # one name, two bodies
            "int counter;\n"
            "__attribute__((noinline)) static int twice(int x) { return 2 * x; }\n"
            "int PyInit__common(void) { return twice(shared_helper()) + counter; }\n" % value
        )
        obj = tmp_path / f"{pkg}.o"
        subprocess.run(
            [tools["clang"], "--target=wasm32", "-O1", "-c", str(src), "-o", str(obj)], check=True
        )
        objs[pkg] = obj

    def link(*inputs):
        return subprocess.run(
            [tools["wasm-ld"], "-r", *map(str, inputs), "-o", str(tmp_path / "all.o")],
            capture_output=True,
            text=True,
        )

    clash = link(objs["a"], objs["b"])
    assert clash.returncode != 0 and "duplicate symbol" in clash.stderr
    for pkg, obj in objs.items():
        wp.localize_object(obj, obj, f"{pkg}._common")
    ok = link(objs["a"], objs["b"])
    assert ok.returncode == 0, ok.stderr
    names = wp.defined_globals((tmp_path / "all.o").read_bytes())
    assert sorted(names) == ["PyInit__x_a___common", "PyInit__x_b___common"]
