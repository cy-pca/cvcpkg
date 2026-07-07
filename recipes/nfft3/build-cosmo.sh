#!/usr/bin/env bash
# recipes/nfft3/build-cosmo.sh — cross-compile NFFT3 with Cosmopolitan.
# Threads and OpenMP disabled.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

if [[ -n "${CVC_DEPS_PREFIX:-}" && -d "${CVC_DEPS_PREFIX}/include" ]]; then
    FFTW_PREFIX="${CVC_DEPS_PREFIX}"
else
    FFTW_PREFIX="${CVC_INSTALL_DIR}"
fi

cd "${CVC_SOURCE_DIR}"

export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=x86_64-unknown-cosmo \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --with-pic \
    --disable-examples \
    --disable-applications \
    --disable-openmp \
    --with-fftw3-includedir="${FFTW_PREFIX}/include" \
    --with-fftw3-libdir="${FFTW_PREFIX}/lib" \
    CFLAGS="-O3 -ffast-math"

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
