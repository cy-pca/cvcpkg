#!/usr/bin/env bash
# recipes/lua/build-wasi.sh — cross-compile Lua to wasm32-wasi via wasi-sdk.
#
# Static liblua.a only (no shared libraries on wasi); lua::lua in the CMake
# package links it.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lua-common.sh"

cd "${CVC_SOURCE_DIR}"
lua_detect_version

# env-wasi.sh has exported CC/AR/RANLIB pointing at wasi-sdk clang.
# Use Lua's `posix` target and let those env vars flow through.
WASI_FLAGS="--target=wasm32-wasip1 --sysroot=${CVC_WASI_SDK_DIR}/share/wasi-sysroot"

make -j "${CVC_JOBS}" posix \
    CC="${CC}" AR="${AR} rcu" RANLIB="${RANLIB}" \
    MYCFLAGS="${WASI_FLAGS} ${CFLAGS:-} -DLUA_USE_POSIX" \
    MYLDFLAGS="${WASI_FLAGS}"

make install \
    INSTALL_TOP="${CVC_INSTALL_DIR}" \
    INSTALL_LIB="${CVC_INSTALL_DIR}/lib" \
    INSTALL_INC="${CVC_INSTALL_DIR}/include" \
    INSTALL_MAN="${CVC_INSTALL_DIR}/share/man/man1"

# wasi-linked lua.wasm/luac.wasm need a runtime (wasmtime, etc.); keep
# them for downstream projects that ship a wasi runtime.

lua_write_pkgconfig "An extensible embeddable language (wasi build)" ""
lua_write_cmake_config "" "" "lib/liblua.a" 'm'

cvc_rewrite_install_paths
