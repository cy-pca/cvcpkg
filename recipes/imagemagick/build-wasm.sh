#!/usr/bin/env bash
# recipes/imagemagick/build-wasm.sh — cross-compile ImageMagick to wasm.
# Minimal build: no X11, no external codecs, Q16-HDRI quantum.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# Point pkg-config + libtool at the wasm-deps prefix so --with-png / --with-jpeg
# actually locate libpng / libjpeg / libwebp / libtiff / libfreetype instead of
# silently falling back to "no" (which is what disabled all image codecs the
# first time this recipe was cross-compiled).
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

# emconfigure sets PKG_CONFIG_LIBDIR to emscripten's sysroot pkgconfig only,
# which OVERRIDES PKG_CONFIG_PATH — so pkg-config never sees the wasm-deps
# libpng/libjpeg/libwebp/libtiff/libfreetype/libxml2 .pc files, every
# PKG_CHECK_MODULES probe returns no, and PNG_DELEGATE ends up FALSE
# (the built libMagickCore had 0 png_read_ symbols despite --with-png).
# Point PKG_CONFIG_LIBDIR at both the wasm-deps prefix AND the emscripten
# sysroot, and set CPPFLAGS/LDFLAGS inline so they survive emconfigure's
# child env.
PKG_CONFIG_LIBDIR="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig:${EMSDK}/upstream/emscripten/cache/sysroot/lib/pkgconfig:${EMSDK}/upstream/emscripten/cache/sysroot/local/lib/pkgconfig" \
PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}" \
CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}" \
LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}" \
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
    --with-zlib \
    --with-jpeg \
    --with-png \
    --with-webp \
    --with-tiff \
    --with-freetype \
    --without-jbig \
    --without-raw \
    --without-openjp2 \
    --without-threads \
    --disable-docs

emmake make -j "${CVC_JOBS}"
emmake make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
