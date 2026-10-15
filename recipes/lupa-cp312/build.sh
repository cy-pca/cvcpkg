#!/usr/bin/env bash
# recipes/lupa-cp312/build.sh — build lupa 2.8 FROM SOURCE for the cp312
# interpreter column, against cvcpkg's own Lua 5.4 (recipes/lua) and LuaJIT 2.1
# (recipes/luajit).
#
# WHY NOT THE PYPI WHEEL (or a plain sdist build): lupa's wheels, and its
# default sdist build, compile the Lua/LuaJIT sources vendored in third-party/
# into every extension module. Here lupa.lua54 and lupa.luajit21 instead link
# the shared liblua.so.5.4 / libluajit-5.1.so.2 that the lua and luajit
# recipes ship, by soname out of the merged prefix — one Lua per prefix,
# patched and versioned by cvcpkg, shared with every C/C++ embedder.
#
# MECHANIC (as pyyaml-cp312): `pip wheel --no-build-isolation --no-deps
# --no-index <sdist>` with the PEP-517 backend and Cython provisioned by the
# depends.build edges, then pip-install the wheel into this recipe's staging
# prefix, then an $ORIGIN-relative RUNPATH pass, then an import check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
TAG="lupa-${CVC_PYTHON_ABI}"
echo "${TAG}: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# setuptools and Cython are depends.build columns, so they land in
# CVC_BUILD_PREFIX, which the DEPS-prefix interpreter does not import from.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("setuptools", setuptools.__version__, setuptools.__file__)'
"${PY}" -c 'import Cython; print("Cython", Cython.__version__)'

# ── 3. Point lupa at cvcpkg's Lua 5.4 and LuaJIT 2.1 ─────────────────────────
# One extension per entry (lupa-external-lua.patch); each links a SHARED
# library, so -l must find the .so/.dylib (both recipes also ship a static
# archive; the linker prefers the shared one in the same directory).
find_dep() {   # find_dep RELPATH -> the first of DEPS/BLD that has it
    local root
    for root in "${DEPS}" "${BLD}"; do
        if [ -e "${root}/$1" ]; then echo "${root}"; return 0; fi
    done
    echo "${TAG}: $1 not found under ${DEPS} or ${BLD} — is that recipe in the closure?" >&2
    return 1
}
case "${CVC_PLATFORM}" in
    macos) solib() { echo "lib/lib$1.dylib"; } ;;
    *)     solib() { echo "lib/lib$1.so"; } ;;
esac
LUA_ROOT="$(find_dep include/lua.h)"
LUAJIT_ROOT="$(find_dep include/luajit-2.1/luajit.h)"
find_dep "$(solib lua)" >/dev/null
find_dep "$(solib luajit-5.1)" >/dev/null
grep -q 'LUA_VERSION_NUM[[:space:]]*504' "${LUA_ROOT}/include/lua.h" \
    || { echo "${TAG}: ${LUA_ROOT}/include/lua.h is not Lua 5.4" >&2; exit 1; }

LUPA_EXTERNAL_LUA="$("${PY}" - "${LUA_ROOT}" "${LUAJIT_ROOT}" <<'PYCFG'
import json, sys
lua, luajit = sys.argv[1], sys.argv[2]
print(json.dumps({
    "lua54":    {"include_dirs": [lua + "/include"],
                 "library_dirs": [lua + "/lib"], "libraries": ["lua"]},
    "luajit21": {"include_dirs": [luajit + "/include/luajit-2.1"],
                 "library_dirs": [luajit + "/lib"], "libraries": ["luajit-5.1"]},
}))
PYCFG
)"
export LUPA_EXTERNAL_LUA
echo "${TAG}: LUPA_EXTERNAL_LUA=${LUPA_EXTERNAL_LUA}"
# Regenerate lupa/<module>.c from _lupa.pyx with the cvcpkg Cython rather than
# compiling the C files generated into the sdist.
export LUPA_WITH_CYTHON=true

# ── 4. Build the wheel from the sdist (offline, no build isolation) ─────────
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "${TAG}: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "${TAG}: built $(basename "${WHEEL}")"

# ── 5. Install into THIS recipe's staging prefix ─────────────────────────────
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages; do
    if [ -d "${_cand}" ]; then SITE_PACKAGES="${_cand}"; break; fi
done
[ -n "${SITE_PACKAGES}" ] || { echo "${TAG}: no site-packages under ${CVC_INSTALL_DIR}" >&2; exit 1; }
echo "${TAG}: staged into ${SITE_PACKAGES}"

shopt -s nullglob
_exts=( "${SITE_PACKAGES}"/lupa/lua54*.so "${SITE_PACKAGES}"/lupa/luajit21*.so )
shopt -u nullglob
[ "${#_exts[@]}" -eq 2 ] || {
    echo "${TAG}: expected lupa/lua54*.so and lupa/luajit21*.so, found: ${_exts[*]:-none}" >&2
    ls -la "${SITE_PACKAGES}/lupa" >&2 || true
    exit 1
}

# ── 6. Make the extensions relocatable ───────────────────────────────────────
# They link liblua / libluajit out of the build-time prefix. At the consumer,
# lupa and both libraries land in ONE merged prefix, so the search path is
# $ORIGIN-relative to <prefix>/lib, computed per file (no `realpath
# --relative-to`: GNU-only).
_relpath_to_lib() {
    "${PY}" - "$1" "${CVC_INSTALL_DIR}/lib" <<'PYREL'
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PYREL
}

if [ "${CVC_PLATFORM}" = "macos" ]; then
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "${TAG}: install_name_tool required on macOS but not found" >&2; exit 1; }
    for _so in "${_exts[@]}"; do
        _rel="$(_relpath_to_lib "${_so}")"
        chmod u+w "${_so}" 2>/dev/null || true
        while IFS= read -r _dep; do
            case "${_dep}" in
                "${DEPS}"/*|"${BLD}"/*)
                    install_name_tool -change "${_dep}" \
                        "@rpath/$(basename "${_dep}")" "${_so}" 2>/dev/null || true
                    ;;
            esac
        done < <(otool -L "${_so}" | awk 'NR>1 {print $1}')
        install_name_tool -add_rpath "@loader_path/${_rel}" "${_so}" 2>/dev/null || true
        # arm64 refuses a Mach-O whose signature the rewrites invalidated;
        # re-sign ad hoc if they left it broken (as recipes/cmake does).
        if ! codesign --verify "${_so}" >/dev/null 2>&1; then
            codesign --force --sign - "${_so}"
        fi
    done
else
    # OpenBSD's ld.so ignores $ORIGIN (cvcpkg relocates OpenBSD bundles at
    # install time); stamping it is harmless there and right everywhere else.
    command -v patchelf >/dev/null 2>&1 || {
        echo "${TAG}: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    for _so in "${_exts[@]}"; do
        _rel="$(_relpath_to_lib "${_so}")"
        patchelf --set-rpath "\$ORIGIN/${_rel}" "${_so}"
        echo "${TAG}: $(basename "${_so}") RUNPATH \$ORIGIN/${_rel}"
    done
fi

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 7. Verification ──────────────────────────────────────────────────────────
# Import both modules in ONE process, run Lua in each, and prove each one runs
# on the cvcpkg library it was linked to (not a copy compiled in): the shared
# objects must be mapped, and each runtime must report its own dialect.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# Do not leave __pycache__ (absent from the wheel RECORD) in the staged tree.
export PYTHONDONTWRITEBYTECODE=1
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
    export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
"${PY}" - <<'PYCHECK'
import os, sys

import lupa
import lupa.lua54 as L54
import lupa.luajit21 as LJ

assert L54.LuaRuntime().eval("1+1") == 2
rt = L54.LuaRuntime()
assert rt.eval("_VERSION") == "Lua 5.4", rt.eval("_VERSION")
assert rt.eval("math.type(3)") == "integer"          # 5.3+ integers
assert rt.eval("7 // 2") == 3
print("lupa.lua54:", rt.eval("_VERSION"), "->", L54.__file__)

jt = LJ.LuaRuntime()
assert jt.eval("1+1") == 2
assert jt.eval("_VERSION") == "Lua 5.1", jt.eval("_VERSION")
assert jt.eval("jit.version").startswith("LuaJIT 2.1"), jt.eval("jit.version")
assert jt.eval("loadstring('return 7 // 2') == nil") is True     # no // in Lua 5.1
print("lupa.luajit21:", jt.eval("jit.version"), "->", LJ.__file__)

# Python <-> Lua round trip through a table and a callback.
t = rt.eval("{10, 20, 30}")
assert list(t.values()) == [10, 20, 30]
assert rt.eval("function(f) return f(21) end")(lambda x: x * 2) == 42

# `import lupa` defaults to the plain-Lua module (upstream's preference order).
assert lupa.LuaRuntime().eval("_VERSION") == "Lua 5.4"

# Linked, not compiled in: both cvcpkg libraries are mapped into the process.
if sys.platform.startswith("linux"):
    maps = open("/proc/self/maps").read()
    for lib in ("liblua.so.5.4", "libluajit-5.1.so.2"):
        assert lib in maps, lib + " is not mapped: lupa did not link the cvcpkg library"
    print("mapped: liblua.so.5.4, libluajit-5.1.so.2")
print("lupa", lupa.__version__, "OK")
PYCHECK

echo "${TAG}: build + verification complete"
