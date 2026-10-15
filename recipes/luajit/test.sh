#!/usr/bin/env bash
# recipes/luajit/test.sh — prove the luajit bundle embeds, and that it relocates.
#
# Same shape as recipes/lua/test.sh: copy the staged prefix elsewhere, check no
# file in the copy names a build-time path, then build a small C program that
# embeds LuaJIT (luaL_newstate, luaL_dostring, read a global, check the jit
# library is there) against the RELOCATED copy -- through
# find_package(luajit 2.1 CONFIG) -> luajit::luajit / luajit::luajit_static and
# through pkg-config luajit, shared and static -- and run each with the loader
# path cleared. Finally run the relocated luajit, including `luajit -b` with
# LUA_PATH pointed at the shipped jit.* modules.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

P="${CVC_INSTALL_DIR%/}"
PLAT="${CVC_PLATFORM:-linux}"
echo "-- luajit smoke test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

INC="include/luajit-2.1"
for h in lua.h luaconf.h lualib.h lauxlib.h lua.hpp luajit.h; do
    test -f "${P}/${INC}/${h}" || fail "${INC}/${h} missing"
done
test -f "${P}/lib/cmake/luajit/luajit-config.cmake" || fail "lib/cmake/luajit/luajit-config.cmake missing"
test -f "${P}/lib/pkgconfig/luajit.pc" || fail "lib/pkgconfig/luajit.pc missing"
ok "headers, CMake package and pkg-config file present"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT

# ── 1. Relocate ──────────────────────────────────────────────────────────────
R="${T}/elsewhere/prefix"
mkdir -p "${T}/elsewhere"
cp -RPp "${P}" "${R}"
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

unset CMAKE_PREFIX_PATH PKG_CONFIG_PATH LUA_PATH LUA_CPATH || true

# Run a CONSUMER (or the relocated luajit) with the loader path cleared, so only
# RUNPATH/@rpath can find libluajit. The build tools keep cvcpkg's environment:
# on OpenBSD (no $ORIGIN) the cvcpkg-built cmake needs LD_LIBRARY_PATH for its
# own libssl. A subshell, since OpenBSD's env(1) has no -u.
noldp() { ( unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH; exec "$@" ); }

if [[ "${PLAT}" == "windows" ]]; then
    LJ="${R}/bin/luajit.exe"
else
    LJ="${R}/bin/luajit"
fi

# ── The relocated interpreter ────────────────────────────────────────────────
# LuaJIT is the Lua 5.1 dialect (what Redis/Valkey EVAL runs): _VERSION is
# "Lua 5.1", setfenv exists, and 5.3's // operator does not parse.
noldp "${LJ}" -e '
assert(_VERSION == "Lua 5.1", _VERSION)
assert(type(setfenv) == "function")
assert(loadstring("return 7 // 2") == nil, "// must not parse in Lua 5.1")
assert(jit and jit.status())
local ffi = require("ffi")
assert(ffi.sizeof("int32_t") == 4)
print("relocated " .. jit.version .. " (" .. jit.os .. "/" .. jit.arch .. "): Lua 5.1 dialect, JIT on, FFI OK")
' || fail "relocated luajit failed"
# The jit.* modules (-b, -jv, -jdump, -jp) live in share/luajit-2.1 on Unix and
# bin/lua/jit on Windows (found there without LUA_PATH).
if [[ "${PLAT}" == "windows" ]]; then
    noldp "${LJ}" -b -e 'return 1 + 1' "${T}/two.raw" || fail "luajit -b failed"
else
    LUA_PATH="${R}/share/luajit-2.1/?.lua;;" noldp "${LJ}" -b -e 'return 1 + 1' "${T}/two.raw" \
        || fail "luajit -b with LUA_PATH=<prefix>/share/luajit-2.1 failed"
fi
# Inside a Lua string the path is not MSYS-converted on Windows; hand luajit.exe
# the native spelling there.
TL="$(cygpath -m "${T}" 2>/dev/null || echo "${T}")"
[[ "$(noldp "${LJ}" -e "print(dofile('${TL}/two.raw'))" | tr -d '\r')" == "2" ]] || fail "bytecode from luajit -b did not run"
ok "luajit -b (jit.bcsave) wrote bytecode that runs"

# ── 2. The embedding program ─────────────────────────────────────────────────
mkdir -p "${T}/consumer"
cat > "${T}/consumer/embed.c" <<'EOF'
#include <stdio.h>
#include <string.h>
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
#include "luajit.h"

int main(void) {
    lua_State *L = luaL_newstate();
    if (L == NULL) { fprintf(stderr, "luaL_newstate failed\n"); return 1; }
    luaL_openlibs(L);
    if (luaL_dostring(L, "answer = 6 * 7\n"
                         "engine = jit.version .. ' ' .. _VERSION")) {
        fprintf(stderr, "luaL_dostring: %s\n", lua_tostring(L, -1));
        return 1;
    }
    lua_getglobal(L, "answer");
    lua_Integer answer = lua_tointeger(L, -1);
    lua_getglobal(L, "engine");
    const char *engine = lua_tostring(L, -1);
    int ok = answer == 42 && engine != NULL
             && strncmp(engine, "LuaJIT 2.1.", 11) == 0
             && strstr(engine, "Lua 5.1") != NULL;
    printf("embedded %s: answer=%lld engine='%s' -> %s\n", LUAJIT_VERSION,
           (long long)answer, engine ? engine : "(null)", ok ? "ok" : "MISMATCH");
    lua_close(L);
    return ok ? 0 : 1;
}
EOF
cat > "${T}/consumer/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(luajit_embed_smoke C)

foreach(_wrong 2.0 2.2)
  find_package(luajit ${_wrong} CONFIG QUIET)
  if(luajit_FOUND)
    message(FATAL_ERROR "find_package(luajit ${_wrong}) accepted LuaJIT ${luajit_VERSION}")
  endif()
endforeach()
unset(luajit_DIR CACHE)

find_package(luajit 2.1 CONFIG REQUIRED)
message(STATUS "luajit ${luajit_VERSION} from ${luajit_DIR}")
get_filename_component(_got "${luajit_DIR}" REALPATH)
get_filename_component(_want "${EXPECTED_PREFIX}/lib/cmake/luajit" REALPATH)
if(NOT _got STREQUAL _want)
  message(FATAL_ERROR "found ${_got}, expected ${_want}")
endif()

add_executable(embed_shared embed.c)
target_link_libraries(embed_shared PRIVATE luajit::luajit)
if(TARGET luajit::luajit_static)
  add_executable(embed_static embed.c)
  target_link_libraries(embed_static PRIVATE luajit::luajit_static)
endif()
EOF

liblj_of() {
    case "${PLAT}" in
        macos)   otool -L "$1" | awk '/libluajit/ {print $1}' ;;
        windows) : ;;
        # "name => path (addr)" on Linux/FreeBSD/NetBSD; a table with the
        # path in the last column on OpenBSD.
        *)       noldp ldd "$1" | awk '/libluajit/ { if (index($0, "=>")) print $3; else print $NF }' ;;
    esac
}
expect_in_reloc() {
    local got
    got="$(liblj_of "$1")"
    case "${PLAT}" in
        macos)   [[ "${got}" == "@rpath/libluajit-5.1.2.dylib" ]] \
                     || fail "$(basename "$1") records libluajit as '${got}'" ;;
        windows) ;;
        *)       [[ -n "${got}" ]] || fail "$(basename "$1") does not load libluajit"
                 [[ "$(cd "$(dirname "${got}")" && pwd -P)" == "$(cd "${R}/lib" && pwd -P)" ]] \
                     || fail "$(basename "$1") loads libluajit from ${got}, not the relocated prefix" ;;
    esac
}
expect_static() {
    local got
    got="$(liblj_of "$1")"
    [[ -z "${got}" ]] || fail "$(basename "$1") should be static but loads ${got}"
}

if [[ "${PLAT}" == "windows" ]]; then
    if [[ -n "${CVC_WINHOST:-}" ]] || ! command -v cl >/dev/null 2>&1; then
        echo "  WARN: no cl.exe in the test shell (or host-delegated build); CMake consumer skipped"
        echo "-- luajit smoke test passed --"
        exit 0
    fi
fi

command -v cmake >/dev/null 2>&1 || fail "cmake not on PATH (it is a declared build dependency)"
gen=()
_ninja="$(command -v ninja 2>/dev/null || true)"
[[ -n "${_ninja}" ]] && gen=(-G Ninja "-DCMAKE_MAKE_PROGRAM=${_ninja}")
cmake ${gen[@]+"${gen[@]}"} -S "${T}/consumer" -B "${T}/build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="${R}" -DEXPECTED_PREFIX="${R}" \
    || fail "CMake configure against the relocated prefix failed"
cmake --build "${T}/build" --config Release || fail "CMake build failed"
exe_path() {
    local f
    for f in "${T}/build/$1" "${T}/build/$1.exe" "${T}/build/Release/$1.exe"; do
        [[ -f "${f}" ]] && { echo "${f}"; return 0; }
    done
    return 1
}
exe="$(exe_path embed_shared)" || fail "embed_shared not built"
if [[ "${PLAT}" == "windows" ]]; then
    PATH="${R}/bin:${PATH}" "${exe}" || fail "embed_shared (luajit::luajit) failed"
    ok "find_package(luajit 2.1): luajit::luajit built and ran from the relocated prefix"
    echo "-- luajit smoke test passed --"
    exit 0
fi
noldp "${exe}" || fail "embed_shared (luajit::luajit) failed"
expect_in_reloc "${exe}"
exe="$(exe_path embed_static)" || fail "embed_static not built"
noldp "${exe}" || fail "embed_static (luajit::luajit_static) failed"
expect_static "${exe}"
ok "find_package(luajit 2.1): luajit::luajit and luajit::luajit_static built and ran from the relocated prefix"

# ── 3. pkg-config consumer ───────────────────────────────────────────────────
command -v pkg-config >/dev/null 2>&1 || fail "pkg-config not on PATH (it is a declared build dependency)"
CCMD="${CC:-cc}"
pc() { PKG_CONFIG_LIBDIR="${R}/lib/pkgconfig" PKG_CONFIG_PATH="" pkg-config "$@"; }
[[ "$(pc --modversion luajit)" == 2.1.* ]] || fail "pkg-config luajit version is $(pc --modversion luajit)"
libdir="$(pc --variable=libdir luajit)"
[[ "$(cd "${libdir}" && pwd -P)" == "$(cd "${R}/lib" && pwd -P)" ]] \
    || fail "luajit.pc libdir ${libdir} is not the relocated prefix"
# shellcheck disable=SC2046
${CCMD} -o "${T}/pc_shared" "${T}/consumer/embed.c" $(pc --cflags luajit) $(pc --libs luajit) \
    "-Wl,-rpath,${libdir}" || fail "pkg-config shared link failed"
static_extra=()
for tok in $(pc --static --libs luajit); do
    case "${tok}" in -L*|-lluajit-5.1) ;; *) static_extra+=("${tok}") ;; esac
done
# shellcheck disable=SC2046
${CCMD} -o "${T}/pc_static" "${T}/consumer/embed.c" $(pc --cflags luajit) "${libdir}/libluajit-5.1.a" \
    ${static_extra[@]+"${static_extra[@]}"} || fail "pkg-config static link failed"
noldp "${T}/pc_shared" || fail "pkg-config shared program failed"
expect_in_reloc "${T}/pc_shared"
noldp "${T}/pc_static" || fail "pkg-config static program failed"
expect_static "${T}/pc_static"
ok "pkg-config luajit: shared and static programs built and ran from the relocated prefix"

echo "-- luajit smoke test passed --"
