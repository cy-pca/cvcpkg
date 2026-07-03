#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# ImageMagick uses autotools. Build with Q16-HDRI (16-bit + high
# dynamic range) to match the existing bundle configuration.
cd "${CVC_SOURCE_DIR}"

# On BSDs, "make" is BSD make which can't parse ImageMagick's GNU
# Makefiles. Use gmake and tell configure/sub-makes about it.
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
else
    MAKE=make
fi
export MAKE

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --enable-static \
    --with-quantum-depth=16 \
    --enable-hdri \
    --with-magick-plus-plus \
    --without-perl \
    --without-x \
    --without-jpeg \
    --without-png \
    --without-webp \
    --without-jbig \
    --without-raw \
    --without-openjp2 \
    --disable-docs \
    CFLAGS="${CFLAGS:-"-O2 -fPIC"}" \
    CXXFLAGS="${CXXFLAGS:-"-O2 -fPIC -std=c++17"}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
