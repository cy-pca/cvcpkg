#!/usr/bin/env bash
# recipes/libunistring/build-cosmo.sh — cross-compile libunistring with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --host=x86_64-unknown-cosmo
    --build="${BUILD_TRIPLET}"
    --disable-shared
    --enable-static
    --disable-dependency-tracking
)
if [[ -d "${CVC_DEPS_PREFIX:-}/include" ]]; then
    CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
