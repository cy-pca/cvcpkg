#!/usr/bin/env bash
# recipes/lua/build-wasi.sh — cross-compile Lua to wasm32-wasi via wasi-sdk.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

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

mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/lua5.4.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: Lua
Description: An extensible embeddable language (wasi build)
URL: https://www.lua.org/
Version: 5.4.7
Libs: -L\${libdir} -llua
Cflags: -I\${includedir}
PC
ln -sf lua5.4.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/lua.pc"
ln -sf lua5.4.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/lua-5.4.pc"

cvc_rewrite_install_paths
