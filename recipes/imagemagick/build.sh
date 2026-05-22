#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# ImageMagick uses autotools. Build with Q16-HDRI (16-bit + high
# dynamic range) to match the existing bundle configuration.
cd "${CVC_SOURCE_DIR}"
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --enable-static \
    --with-quantum-depth=16 \
    --enable-hdri \
    --with-magick-plus-plus \
    --without-perl \
    --without-x \
    --disable-docs \
    CFLAGS="${CFLAGS:-"-O2 -fPIC"}" \
    CXXFLAGS="${CXXFLAGS:-"-O2 -fPIC -std=c++17"}"
make -j "${CVC_JOBS}"
make install
