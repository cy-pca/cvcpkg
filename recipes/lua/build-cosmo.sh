#!/usr/bin/env bash
# recipes/lua/build-cosmo.sh — cross-compile Lua with Cosmopolitan.
#
# Static liblua.a + APE lua/luac; lua::lua in the CMake package links the
# static library (cosmo has no shared libraries).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lua-common.sh"

cd "${CVC_SOURCE_DIR}"
lua_detect_version

make -j "${CVC_JOBS}" generic \
    CC="${CC}" AR="${AR} rcu" RANLIB="${RANLIB}" \
    MYCFLAGS="${CFLAGS:-} -DLUA_USE_POSIX" \
    MYLDFLAGS=""

make install \
    INSTALL_TOP="${CVC_INSTALL_DIR}" \
    INSTALL_LIB="${CVC_INSTALL_DIR}/lib" \
    INSTALL_INC="${CVC_INSTALL_DIR}/include" \
    INSTALL_BIN="${CVC_INSTALL_DIR}/bin" \
    INSTALL_MAN="${CVC_INSTALL_DIR}/share/man/man1"

lua_write_pkgconfig "An extensible embeddable language (cosmo build)" "-lm"
lua_write_cmake_config "" "" "lib/liblua.a" 'm'

cvc_rewrite_install_paths
