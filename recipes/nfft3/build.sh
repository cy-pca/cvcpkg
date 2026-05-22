#!/usr/bin/env bash
# recipes/nfft3/build.sh — build NFFT3 from upstream autotools tarball.
# Source is fetched by cvcpkg and available at $CVC_SOURCE_DIR.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# ── Locate FFTW3 ──
# When building in a shared prefix, FFTW3 is already installed at
# $CVC_DEPS_PREFIX (set by cvcpkg builder).  On macOS standalone
# builds, fall back to Homebrew.
if [[ -n "${CVC_DEPS_PREFIX:-}" && -d "${CVC_DEPS_PREFIX}/include" ]]; then
    FFTW_PREFIX="${CVC_DEPS_PREFIX}"
elif [[ "${CVC_PLATFORM}" == "macos" ]]; then
    FFTW_PREFIX="$(brew --prefix fftw 2>/dev/null || echo /opt/homebrew)"
else
    FFTW_PREFIX="${CVC_INSTALL_DIR}"
fi

# ── Platform-specific configure flags ──
CONFIGURE_CC=""
OPENMP_FLAG="--enable-openmp"
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    CONFIGURE_CC="CC=clang"
    OPENMP_FLAG="--disable-openmp"
fi

# ── Configure & build ──
cd "${CVC_SOURCE_DIR}"
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --enable-static \
    --with-pic \
    --disable-examples \
    --disable-applications \
    ${OPENMP_FLAG} \
    --with-fftw3-includedir="${FFTW_PREFIX}/include" \
    --with-fftw3-libdir="${FFTW_PREFIX}/lib" \
    CFLAGS="-O3 -ffast-math" \
    ${CONFIGURE_CC}

make -j"$JOBS"
make install

# ── Rewrite .pc for relocatability ──
PC="${CVC_INSTALL_DIR}/lib/pkgconfig/nfft3.pc"
if [ -f "$PC" ]; then
    sed -i.bak \
        -e 's|^prefix=.*|prefix=${pcfiledir}/../..|' \
        -e 's|^exec_prefix=.*|exec_prefix=${prefix}|' \
        -e 's|^libdir=.*|libdir=${prefix}/lib|' \
        -e 's|^includedir=.*|includedir=${prefix}/include|' \
        "$PC"
    rm -f "${PC}.bak"
fi

echo "NFFT3 installed to ${CVC_INSTALL_DIR}"
