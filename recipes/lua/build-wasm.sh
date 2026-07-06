#!/usr/bin/env bash
# recipes/lua/build-wasm.sh — cross-compile Lua to wasm via Emscripten.
#
# Uses Lua's `generic` Makefile target (no readline / mmap dependency).
# Produces liblua.a — wasm has no shared library concept, so the
# .so target is skipped.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

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

# Synthesise pkg-config file (upstream doesn't ship one).
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/lua5.4.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: Lua
Description: An extensible embeddable language (wasm build)
URL: https://www.lua.org/
Version: 5.4.7
Libs: -L\${libdir} -llua
Cflags: -I\${includedir}
PC
ln -sf lua5.4.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/lua.pc"
ln -sf lua5.4.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/lua-5.4.pc"

cvc_rewrite_install_paths
