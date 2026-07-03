#!/usr/bin/env bash
# recipes/imagemagick/build-wasm.sh — cross-compile ImageMagick to wasm.
# Minimal build: no X11, no external codecs, Q16-HDRI quantum.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --disable-shared \
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
    --without-threads \
    --disable-docs

emmake make -j "${CVC_JOBS}"
emmake make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
