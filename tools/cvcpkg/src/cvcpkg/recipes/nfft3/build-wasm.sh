#!/usr/bin/env bash
# recipes/nfft3/build-wasm.sh — cross-compile NFFT3 to wasm.
# Threads and OpenMP disabled for wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

if [[ -n "${CVC_DEPS_PREFIX:-}" && -d "${CVC_DEPS_PREFIX}/include" ]]; then
    FFTW_PREFIX="${CVC_DEPS_PREFIX}"
else
    FFTW_PREFIX="${CVC_INSTALL_DIR}"
fi

cd "${CVC_SOURCE_DIR}"

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --disable-shared \
    --enable-static \
    --with-pic \
    --disable-examples \
    --disable-applications \
    --disable-openmp \
    --with-fftw3-includedir="${FFTW_PREFIX}/include" \
    --with-fftw3-libdir="${FFTW_PREFIX}/lib" \
    CFLAGS="-O3 -ffast-math"

emmake make -j "${CVC_JOBS}"
emmake make install
