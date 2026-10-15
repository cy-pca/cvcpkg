#!/usr/bin/env bash
# recipes/lua/build-wasm.sh — cross-compile Lua to wasm via Emscripten.
#
# Uses Lua's `generic` Makefile target (no readline / mmap dependency).
# Produces liblua.a only — wasm has no shared library concept, so lua::lua in
# the CMake package links the static library here.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lua-common.sh"

cd "${CVC_SOURCE_DIR}"
lua_detect_version

# Emscripten replaces CC/AR/RANLIB; env-wasm.sh has already sourced
# emsdk_env.sh so emcc/emar/emranlib are on PATH.
make -j "${CVC_JOBS}" generic \
    CC=emcc AR="emar rcu" RANLIB=emranlib \
    MYCFLAGS="${CFLAGS:-} -DLUA_USE_POSIX" \
    MYLDFLAGS=""

make install \
    INSTALL_TOP="${CVC_INSTALL_DIR}" \
    INSTALL_LIB="${CVC_INSTALL_DIR}/lib" \
    INSTALL_INC="${CVC_INSTALL_DIR}/include" \
    INSTALL_MAN="${CVC_INSTALL_DIR}/share/man/man1"

# Wasm-native binaries can't be exec'd on the host — drop them.
rm -f "${CVC_INSTALL_DIR}/bin/lua" "${CVC_INSTALL_DIR}/bin/luac"
rmdir "${CVC_INSTALL_DIR}/bin" 2>/dev/null || true

# pkg-config + CMake package (static only), from the shared templates.
lua_write_pkgconfig "An extensible embeddable language (wasm build)" ""
lua_write_cmake_config "" "" "lib/liblua.a" 'm'

cvc_rewrite_install_paths
