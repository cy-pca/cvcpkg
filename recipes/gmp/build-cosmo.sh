#!/usr/bin/env bash
# recipes/gmp/build-cosmo.sh — cross-compile GMP with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

# GMP cross-compilation needs CC_FOR_BUILD for host-side code generators.
export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=x86_64-unknown-cosmo \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --enable-cxx \
    --disable-assembly

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
