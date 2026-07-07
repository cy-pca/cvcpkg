#!/usr/bin/env bash
# recipes/lua/build-cosmo.sh — cross-compile Lua with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

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

cvc_rewrite_install_paths
