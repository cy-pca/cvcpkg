#!/usr/bin/env bash
# Build NFFT3 from upstream autotools tarball (Linux & macOS).
# Mirrors the CI logic in .github/workflows/release.yml.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../_common/env-$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/darwin/macos/').sh"

NFFT_VERSION="3.5.3"
NFFT_SHA256="caf1b3b3e5bf8c33a6bfd7eca811d954efce896605ecfd0144d47d0bebdf4371"

PREFIX="${CVC_PREFIX:?CVC_PREFIX must be set}"
JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# ── Fetch source ──
URL_GH="https://github.com/NFFT/nfft/releases/download/${NFFT_VERSION}/nfft-${NFFT_VERSION}.tar.gz"
URL_TUC="https://www-user.tu-chemnitz.de/~potts/nfft/download/nfft-${NFFT_VERSION}.tar.gz"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

curl -fsSL "$URL_GH" -o nfft.tar.gz || curl -fsSL "$URL_TUC" -o nfft.tar.gz
echo "${NFFT_SHA256}  nfft.tar.gz" | shasum -a 256 -c -
tar -xzf nfft.tar.gz
cd "nfft-${NFFT_VERSION}"

# ── Locate FFTW3 ──
if [[ "$(uname -s)" == "Darwin" ]]; then
    FFTW_PREFIX="$(brew --prefix fftw 2>/dev/null || echo /opt/homebrew)"
    CONFIGURE_CC="CC=clang"
    OPENMP_FLAG="--disable-openmp"
else
    FFTW_PREFIX="${CVC_PREFIX}"
    CONFIGURE_CC=""
    OPENMP_FLAG="--enable-openmp"
fi

# ── Configure & build ──
./configure \
    --prefix="$PREFIX" \
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
PC="$PREFIX/lib/pkgconfig/nfft3.pc"
if [ -f "$PC" ]; then
    sed -i.bak \
        -e 's|^prefix=.*|prefix=${pcfiledir}/../..|' \
        -e 's|^exec_prefix=.*|exec_prefix=${prefix}|' \
        -e 's|^libdir=.*|libdir=${prefix}/lib|' \
        -e 's|^includedir=.*|includedir=${prefix}/include|' \
        "$PC"
    rm -f "${PC}.bak"
fi

echo "NFFT3 ${NFFT_VERSION} installed to ${PREFIX}"
