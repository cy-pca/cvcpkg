"""cpkg (getcpkg.net) integration — resolve cvcpkg prebuilt binaries for cpkg.lua.

`cpkg <https://getcpkg.net/>`_ is a Lua + Ninja project/dependency tool for
C/C++.  This module lets a ``cpkg.lua`` build script pull a pinned, prebuilt
binary from the cvcpkg archive into the project prefix instead of building the
dependency from source, via the ``cvcpkg cpkg deps`` command and the companion
``integrations/cpkg/cvcpkg.lua`` shim.

The heavy lifting (resolution, download, signature checks, mirrors) is reused
from ``cvcpkg install``; this module only *scans* the resulting prefix and
serialises the include/lib/pkg-config/cmake locations into a form cpkg can
consume (a Lua table literal or JSON).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Library file extensions we surface as linkable names.
_LIB_SUFFIXES = (".so", ".a", ".dylib", ".lib")
# Prefixes stripped when deriving a link name from a library filename.
_LIB_NAME_PREFIXES = ("lib",)


@dataclass
class PrefixInfo:
    """cpkg-consumable description of an installed cvcpkg prefix."""

    prefix: str
    include_dirs: list[str] = field(default_factory=list)
    lib_dirs: list[str] = field(default_factory=list)
    libs: list[str] = field(default_factory=list)
    pkgconfig_dirs: list[str] = field(default_factory=list)
    cmake_dirs: list[str] = field(default_factory=list)
    bin_dir: str = ""


def _lib_link_name(filename: str) -> str | None:
    """Derive a linker name from a library filename, or None if not a lib.

    ``libboost_system.so.1.83.0`` -> ``boost_system``;
    ``zlib.lib`` -> ``zlib``.  Import libs and versioned SOs are handled.
    """
    name = filename
    # Strip the first library suffix we recognise (handles versioned .so.N).
    lower = name.lower()
    cut = -1
    for suf in _LIB_SUFFIXES:
        idx = lower.find(suf)
        if idx != -1 and (cut == -1 or idx < cut):
            cut = idx
    if cut == -1:
        return None
    stem = name[:cut]
    for pre in _LIB_NAME_PREFIXES:
        if stem.startswith(pre) and len(stem) > len(pre):
            stem = stem[len(pre) :]
            break
    return stem or None


def scan_prefix(prefix: Path) -> PrefixInfo:
    """Scan an installed cvcpkg prefix into a :class:`PrefixInfo`.

    Pure filesystem inspection — no network, no install.  Missing
    subdirectories are simply omitted, so a partial prefix scans cleanly.
    """
    prefix = Path(prefix)
    info = PrefixInfo(prefix=str(prefix))

    inc = prefix / "include"
    if inc.is_dir():
        info.include_dirs.append(str(inc))

    lib_dirs = [d for d in (prefix / "lib", prefix / "lib64") if d.is_dir()]
    info.lib_dirs = [str(d) for d in lib_dirs]

    # Linkable library names (deduped, sorted for determinism).
    libs: set[str] = set()
    for d in lib_dirs:
        for entry in d.iterdir():
            if entry.is_dir():
                continue
            link = _lib_link_name(entry.name)
            if link:
                libs.add(link)
    info.libs = sorted(libs)

    # pkg-config and cmake package dirs (search the standard locations).
    pc_dirs = []
    for cand in (
        prefix / "lib" / "pkgconfig",
        prefix / "lib64" / "pkgconfig",
        prefix / "share" / "pkgconfig",
    ):
        if cand.is_dir():
            pc_dirs.append(str(cand))
    info.pkgconfig_dirs = pc_dirs

    cmake_dirs = []
    for cand in (prefix / "lib" / "cmake", prefix / "lib64" / "cmake", prefix / "cmake"):
        if cand.is_dir():
            cmake_dirs.append(str(cand))
    info.cmake_dirs = cmake_dirs

    b = prefix / "bin"
    if b.is_dir():
        info.bin_dir = str(b)

    return info


def _lua_quote(s: str) -> str:
    """Quote a string as a Lua long-bracket-safe double-quoted literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lua_array(items: list[str]) -> str:
    return "{ " + ", ".join(_lua_quote(i) for i in items) + " }"


def to_lua(info: PrefixInfo) -> str:
    """Serialise *info* as a ``return { ... }`` Lua chunk.

    The companion ``cvcpkg.lua`` shim does ``load(output)()`` to get a table
    with ``prefix``, ``include_dirs``, ``lib_dirs``, ``libs``,
    ``pkgconfig_dirs``, ``cmake_dirs``, and ``bin_dir`` fields.
    """
    lines = [
        "return {",
        f"  prefix = {_lua_quote(info.prefix)},",
        f"  include_dirs = {_lua_array(info.include_dirs)},",
        f"  lib_dirs = {_lua_array(info.lib_dirs)},",
        f"  libs = {_lua_array(info.libs)},",
        f"  pkgconfig_dirs = {_lua_array(info.pkgconfig_dirs)},",
        f"  cmake_dirs = {_lua_array(info.cmake_dirs)},",
        f"  bin_dir = {_lua_quote(info.bin_dir)},",
        "}",
    ]
    return "\n".join(lines) + "\n"


def to_json(info: PrefixInfo) -> str:
    """Serialise *info* as pretty JSON (for non-Lua consumers)."""
    return json.dumps(asdict(info), indent=2, sort_keys=True) + "\n"
