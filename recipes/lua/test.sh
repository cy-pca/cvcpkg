#!/usr/bin/env bash
# recipes/lua/test.sh — prove the lua bundle embeds, and that it relocates.
#
# Run by cvcpkg on the builder after build.sh, with CVC_INSTALL_DIR holding the
# staged tree. Native platforms:
#
#   1. copy the staged prefix somewhere else (the "relocated" prefix) and check
#      that no file in it mentions the build-time paths;
#   2. build a small C program that embeds Lua (luaL_newstate, luaL_dostring,
#      read a global back) plus a C++ one (lua.hpp) against the RELOCATED copy,
#      through find_package(lua 5.4 CONFIG) -> lua::lua and lua::lua_static,
#      and through pkg-config lua5.4, shared and static;
#   3. run every program with the loader path cleared and check where liblua
#      was loaded from (the relocated prefix) or that it was not loaded at all
#      (the static ones);
#   4. run the relocated bin/lua and bin/luac, and load a C module built
#      against the headers into bin/lua (it resolves the Lua API from the
#      shared liblua the interpreter itself uses).
#
# wasm/wasi compile and run the same embedding program under node/wasmtime;
# cosmo checks presence (APE binaries do not run in the builder sandbox).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
# shellcheck disable=SC1091
source "${_COMMON_DIR}/cvc_wasm_run.sh"

P="${CVC_INSTALL_DIR%/}"
PLAT="${CVC_PLATFORM:-linux}"
echo "-- lua smoke test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

for h in lua.h luaconf.h lualib.h lauxlib.h lua.hpp; do
    test -f "${P}/include/${h}" || fail "include/${h} missing"
done
test -f "${P}/lib/cmake/lua/lua-config.cmake" || fail "lib/cmake/lua/lua-config.cmake missing"
test -f "${P}/lib/cmake/lua/lua-config-version.cmake" || fail "lua-config-version.cmake missing"
test -f "${P}/lib/pkgconfig/lua5.4.pc" || fail "lib/pkgconfig/lua5.4.pc missing"
ok "headers, CMake package and pkg-config file present"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT

# ── The embedding program every leg builds ──────────────────────────────────
cat > "${T}/embed.c" <<'EOF'
#include <stdio.h>
#include <string.h>
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

int main(void) {
    lua_State *L = luaL_newstate();
    if (L == NULL) { fprintf(stderr, "luaL_newstate failed\n"); return 1; }
    luaL_openlibs(L);
    if (luaL_dostring(L, "answer = 6 * 7\n"
                         "greeting = string.format('%s says %d', _VERSION, answer)") != LUA_OK) {
        fprintf(stderr, "luaL_dostring: %s\n", lua_tostring(L, -1));
        return 1;
    }
    lua_getglobal(L, "answer");
    lua_Integer answer = lua_tointeger(L, -1);
    lua_getglobal(L, "greeting");
    const char *greeting = lua_tostring(L, -1);
    /* The runtime must be the same Lua the headers describe. */
    int ok = answer == 42 && greeting != NULL
             && strcmp(greeting, LUA_VERSION " says 42") == 0
             && (int)lua_version(L) == LUA_VERSION_NUM;
    printf("embedded %s: answer=%lld greeting='%s' lua_version=%d -> %s\n",
           LUA_RELEASE, (long long)answer, greeting ? greeting : "(null)",
           (int)lua_version(L), ok ? "ok" : "MISMATCH");
    lua_close(L);
    return ok ? 0 : 1;
}
EOF

case "${PLAT}" in
wasm|wasi)
    test -f "${P}/lib/liblua.a" || fail "lib/liblua.a missing"
    if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
        echo "  WARN: ${PLAT} runtime unavailable, skipping compile+run"
    else
        out="${T}/embed.$([[ "${PLAT}" == wasm ]] && echo js || echo wasm)"
        cvc_wasm_cc "${out}" "${T}/embed.c" -llua -lm || fail "${PLAT} compile failed"
        cvc_wasm_run "${out}" || fail "${PLAT} run failed"
        ok "${PLAT} embed compiled and ran under ${CVC_WASM_RUNNER}"
    fi
    echo "-- lua smoke test passed --"
    exit 0
    ;;
cosmo)
    test -f "${P}/lib/liblua.a" || fail "lib/liblua.a missing"
    test -f "${P}/bin/lua" || fail "bin/lua missing"
    ok "liblua.a + bin/lua present (APE binaries are not run in the builder sandbox)"
    echo "-- lua smoke test passed --"
    exit 0
    ;;
esac

# ── 1. Relocate ──────────────────────────────────────────────────────────────
R="${T}/elsewhere/prefix"
mkdir -p "${T}/elsewhere"
cp -RPp "${P}" "${R}"

# Nothing in the relocated tree may still point at where it was built. Check
# the literal and the symlink-resolved spelling of every build-time root.
roots=()
for d in "${P}" "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    [ -n "${d}" ] && [ -d "${d}" ] || continue
    roots+=("${d%/}" "$(cd "${d}" && pwd -P)")
done
for root in ${roots[@]+"${roots[@]}"}; do
    if leaks="$(grep -rlF -- "${root}" "${R}" 2>/dev/null)"; then
        fail "relocated prefix still references ${root}:
${leaks}"
    fi
done
ok "no build-time path in any relocated file"

unset CMAKE_PREFIX_PATH PKG_CONFIG_PATH || true

# Run a CONSUMER with the loader path cleared, so only its RUNPATH/@rpath can
# find liblua. Only the consumers: the build tools (cmake) keep the
# environment cvcpkg gave the test -- on OpenBSD, whose ld.so ignores $ORIGIN,
# the cvcpkg-built cmake needs LD_LIBRARY_PATH to find its own libssl. A
# subshell rather than `env -u`, which OpenBSD's env(1) does not have.
noldp() { ( unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH; exec "$@" ); }

# Where did EXE load liblua from? Prints nothing when it is linked statically.
# ldd prints "name => path (addr)" on Linux/FreeBSD/NetBSD and a table whose
# last column is the path on OpenBSD.
liblua_of() {
    case "${PLAT}" in
        macos)   otool -L "$1" | awk '/liblua/ {print $1}' ;;
        windows) : ;;
        *)       noldp ldd "$1" | awk '/liblua/ { if (index($0, "=>")) print $3; else print $NF }' ;;
    esac
}
# Assert a shared-linked EXE resolves liblua inside the relocated prefix.
expect_liblua_in_reloc() {
    local exe="$1" got
    got="$(liblua_of "${exe}")"
    case "${PLAT}" in
        macos)
            [[ "${got}" == "@rpath/liblua.5.4.dylib" ]] \
                || fail "$(basename "${exe}") records liblua as '${got}', not @rpath/liblua.5.4.dylib" ;;
        windows) ;;
        *)
            [[ -n "${got}" ]] || fail "$(basename "${exe}") does not load liblua"
            [[ "$(cd "$(dirname "${got}")" && pwd -P)" == "$(cd "${R}/lib" && pwd -P)" ]] \
                || fail "$(basename "${exe}") loads liblua from ${got}, not the relocated prefix" ;;
    esac
}
expect_static() {
    local got
    got="$(liblua_of "$1")"
    [[ -z "${got}" ]] || fail "$(basename "$1") should be static but loads ${got}"
}

if [[ "${PLAT}" == "windows" ]]; then
    if [[ -n "${CVC_WINHOST:-}" ]]; then
        echo "  WARN: host-delegated Windows build; consumer compile+run skipped"
        echo "-- lua smoke test passed --"
        exit 0
    fi
    test -f "${R}/bin/lua54.dll" || fail "bin/lua54.dll missing"
    test -f "${R}/lib/lua54.lib" || fail "lib/lua54.lib missing"
    test -f "${R}/lib/lua54_static.lib" || fail "lib/lua54_static.lib missing"
    "${R}/bin/lua.exe" -e 'assert(_VERSION == "Lua 5.4")' || fail "relocated lua.exe failed"
    ok "relocated lua.exe runs"
fi

# ── 2/3. CMake consumer against the relocated prefix ─────────────────────────
mkdir -p "${T}/consumer"
cp "${T}/embed.c" "${T}/consumer/embed.c"
cat > "${T}/consumer/embed.cpp" <<'EOF'
#include <lua.hpp>
#include <iostream>
#include <string>
int main() {
    lua_State *L = luaL_newstate();
    luaL_openlibs(L);
    if (luaL_dostring(L, "return ('cxx'):rep(2) .. (6 * 7)") != LUA_OK) {
        std::cerr << lua_tostring(L, -1) << "\n";
        return 1;
    }
    std::string got = lua_tostring(L, -1);
    lua_close(L);
    std::cout << "embedded from C++: " << got << "\n";
    return got == "cxxcxx42" ? 0 : 1;
}
EOF
cat > "${T}/consumer/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(lua_embed_smoke C CXX)

# The version file must follow Lua's ABI rule: 5.4.x only.
foreach(_wrong 5.3 5.5)
  find_package(lua ${_wrong} CONFIG QUIET)
  if(lua_FOUND)
    message(FATAL_ERROR "find_package(lua ${_wrong}) accepted Lua ${lua_VERSION}")
  endif()
endforeach()
unset(lua_DIR CACHE)

find_package(lua 5.4 CONFIG REQUIRED)
message(STATUS "lua ${lua_VERSION} from ${lua_DIR}")

# It must be the relocated copy, and so must the libraries it names.
get_filename_component(_got "${lua_DIR}" REALPATH)
get_filename_component(_want "${EXPECTED_PREFIX}/lib/cmake/lua" REALPATH)
if(NOT _got STREQUAL _want)
  message(FATAL_ERROR "found ${_got}, expected ${_want}")
endif()
get_filename_component(_prefix "${EXPECTED_PREFIX}" REALPATH)
foreach(_t lua::lua_static lua::lua)
  get_target_property(_type ${_t} TYPE)
  if(_type STREQUAL "INTERFACE_LIBRARY")
    continue()
  endif()
  get_target_property(_loc ${_t} IMPORTED_LOCATION)
  get_filename_component(_loc "${_loc}" REALPATH)
  string(FIND "${_loc}" "${_prefix}/" _at)
  if(NOT _at EQUAL 0 OR NOT EXISTS "${_loc}")
    message(FATAL_ERROR "${_t} -> ${_loc}, not inside ${_prefix}")
  endif()
  message(STATUS "${_t} -> ${_loc}")
endforeach()

add_executable(embed_shared embed.c)
target_link_libraries(embed_shared PRIVATE lua::lua)
add_executable(embed_static embed.c)
target_link_libraries(embed_static PRIVATE lua::lua_static)
add_executable(embed_cxx embed.cpp)
target_link_libraries(embed_cxx PRIVATE lua::lua)
EOF

command -v cmake >/dev/null 2>&1 || fail "cmake not on PATH (it is a declared build dependency)"
gen=()
_ninja="$(command -v ninja 2>/dev/null || true)"
if [[ -n "${_ninja}" ]]; then
    gen=(-G Ninja "-DCMAKE_MAKE_PROGRAM=${_ninja}")
fi
if [[ "${PLAT}" == "windows" ]] && ! command -v cl >/dev/null 2>&1; then
    echo "  WARN: no cl.exe in the test shell; CMake consumer skipped"
    echo "-- lua smoke test passed --"
    exit 0
fi
echo "  tools: cmake=$(command -v cmake) ninja=${_ninja:-none} CC=${CC:-default}"
cmake ${gen[@]+"${gen[@]}"} -S "${T}/consumer" -B "${T}/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="${R}" \
    -DEXPECTED_PREFIX="${R}" \
    || fail "CMake configure against the relocated prefix failed"
cmake --build "${T}/build" --config Release || fail "CMake build failed"

exe_path() {
    local f
    for f in "${T}/build/$1" "${T}/build/$1.exe" "${T}/build/Release/$1.exe"; do
        [[ -f "${f}" ]] && { echo "${f}"; return 0; }
    done
    fail "built executable $1 not found"
}
run_exe() {
    if [[ "${PLAT}" == "windows" ]]; then
        PATH="${R}/bin:${PATH}" "$@"
    else
        noldp "$@"
    fi
}
for t in embed_shared embed_cxx; do
    exe="$(exe_path "${t}")"
    run_exe "${exe}" || fail "${t} (lua::lua) failed"
    expect_liblua_in_reloc "${exe}"
done
exe="$(exe_path embed_static)"
run_exe "${exe}" || fail "embed_static (lua::lua_static) failed"
expect_static "${exe}"
ok "find_package(lua 5.4): lua::lua (C and C++) and lua::lua_static built and ran from the relocated prefix"

if [[ "${PLAT}" == "windows" ]]; then
    echo "-- lua smoke test passed --"
    exit 0
fi

# ── 2/3. pkg-config consumer ─────────────────────────────────────────────────
command -v pkg-config >/dev/null 2>&1 || fail "pkg-config not on PATH (it is a declared build dependency)"
CCMD="${CC:-cc}"
pc() { PKG_CONFIG_LIBDIR="${R}/lib/pkgconfig" PKG_CONFIG_PATH="" pkg-config "$@"; }
[[ "$(pc --modversion lua5.4)" == 5.4.* ]] || fail "pkg-config lua5.4 version is $(pc --modversion lua5.4)"
for alias in lua lua-5.4 lua54; do
    [[ "$(pc --modversion "${alias}")" == "$(pc --modversion lua5.4)" ]] || fail "${alias}.pc broken"
done
libdir="$(pc --variable=libdir lua5.4)"
[[ "$(cd "${libdir}" && pwd -P)" == "$(cd "${R}/lib" && pwd -P)" ]] \
    || fail "lua5.4.pc libdir ${libdir} is not the relocated prefix"
# shellcheck disable=SC2046
${CCMD} -o "${T}/pc_shared" "${T}/embed.c" $(pc --cflags lua5.4) $(pc --libs lua5.4) \
    "-Wl,-rpath,${libdir}" || fail "pkg-config shared link failed"
static_extra=()
for tok in $(pc --static --libs-only-l lua5.4); do
    [[ "${tok}" == "-llua" ]] || static_extra+=("${tok}")
done
# shellcheck disable=SC2046
${CCMD} -o "${T}/pc_static" "${T}/embed.c" $(pc --cflags lua5.4) "${libdir}/liblua.a" \
    ${static_extra[@]+"${static_extra[@]}"} || fail "pkg-config static link failed"
noldp "${T}/pc_shared" || fail "pkg-config shared program failed"
expect_liblua_in_reloc "${T}/pc_shared"
noldp "${T}/pc_static" || fail "pkg-config static program failed"
expect_static "${T}/pc_static"
ok "pkg-config lua5.4: shared and static programs built and ran from the relocated prefix"

# ── 4. The relocated interpreter and compiler ────────────────────────────────
# Runtime deps (readline) live in the deps prefix, not in this bundle; expose
# them -- and nothing named liblua -- so the relocated lua must find liblua
# through its own $ORIGIN/@loader_path RUNPATH.
depsonly="${T}/depsonly"
mkdir -p "${depsonly}"
if [[ -n "${CVC_DEPS_PREFIX:-}" && -d "${CVC_DEPS_PREFIX}/lib" ]]; then
    for f in "${CVC_DEPS_PREFIX}"/lib/*.so* "${CVC_DEPS_PREFIX}"/lib/*.dylib; do
        [[ -e "${f}" ]] || continue
        case "$(basename "${f}")" in liblua.*) continue ;; esac
        ln -sf "${f}" "${depsonly}/"
    done
fi
run_reloc_lua() {
    case "${PLAT}" in
        macos)   ( unset DYLD_LIBRARY_PATH; DYLD_FALLBACK_LIBRARY_PATH="${depsonly}" exec "$@" ) ;;
        # OpenBSD's ld.so does not expand $ORIGIN (cvcpkg relocates OpenBSD
        # bundles at install time instead), so the bundle's lib/ is named here.
        openbsd) LD_LIBRARY_PATH="${R}/lib:${depsonly}" "$@" ;;
        *)       LD_LIBRARY_PATH="${depsonly}" "$@" ;;
    esac
}
run_reloc_lua "${R}/bin/lua" -e 'assert(_VERSION == "Lua 5.4"); print("relocated " .. _VERSION .. " interpreter OK")' \
    || fail "relocated bin/lua failed"
if [[ "${PLAT}" != "openbsd" && "${PLAT}" != "macos" ]]; then
    got="$(LD_LIBRARY_PATH="${depsonly}" ldd "${R}/bin/lua" | awk '/liblua/ { if (index($0, "=>")) print $3; else print $NF }')"
    [[ "$(cd "$(dirname "${got}")" && pwd -P)" == "$(cd "${R}/lib" && pwd -P)" ]] \
        || fail "relocated bin/lua loads liblua from '${got}'"
    ok "relocated bin/lua resolves liblua via \$ORIGIN: ${got}"
fi

printf 'local t = {}\nfor i = 1, 10 do t[#t + 1] = i * i end\nreturn table.concat(t, ",")\n' > "${T}/sq.lua"
run_reloc_lua "${R}/bin/luac" -o "${T}/sq.luac" "${T}/sq.lua" || fail "luac failed"
run_reloc_lua "${R}/bin/lua" -e "assert(dofile('${T}/sq.luac') == '1,4,9,16,25,36,49,64,81,100')" \
    || fail "lua could not run luac's bytecode"
ok "luac bytecode compiled and ran"

# A C module: resolves the Lua API from the interpreter's own shared liblua.
cat > "${T}/twice.c" <<'EOF'
#include "lua.h"
#include "lauxlib.h"
static int twice(lua_State *L) { lua_pushinteger(L, 2 * luaL_checkinteger(L, 1)); return 1; }
int luaopen_twice(lua_State *L) {
    luaL_Reg fns[] = { {"twice", twice}, {NULL, NULL} };
    luaL_newlib(L, fns);
    return 1;
}
EOF
if [[ "${PLAT}" == "macos" ]]; then
    modflags=(-bundle -undefined dynamic_lookup)
else
    modflags=(-shared -fPIC)
fi
# shellcheck disable=SC2046
${CCMD} "${modflags[@]}" -o "${T}/twice.so" "${T}/twice.c" $(pc --cflags lua5.4) \
    || fail "C module build failed"
run_reloc_lua "${R}/bin/lua" -e "package.cpath = '${T}/?.so'; assert(require('twice').twice(21) == 42)" \
    || fail "bin/lua could not load a C module"
ok "bin/lua loaded a C module against its shared liblua"

echo "-- lua smoke test passed --"
