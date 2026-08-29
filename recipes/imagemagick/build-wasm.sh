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

# emconfigure hard-sets PKG_CONFIG_LIBDIR to emscripten's sysroot pkgconfig
# only — pkg-config uses PKG_CONFIG_LIBDIR EXCLUSIVELY when set, so
# PKG_CHECK_MODULES([PNG],[libpng >= 1.0.0]) never sees the wasm-deps
# libpng.pc, PNG_DELEGATE ends up FALSE, and the built libMagickCore has
# 0 png_read_ symbols (despite --with-png). Ditto every other delegate.
#
# Wrap emconfigure in a shell that RE-sets PKG_CONFIG_LIBDIR after
# emconfigure has run its own env manipulation — bash-c's env comes from
# emconfigure's env, but the assignments inside the string run last, so
# they win.
emconfigure bash -c '
    export PKG_CONFIG_LIBDIR="'"${CVC_DEPS_PREFIX}"'/lib/pkgconfig:'"${CVC_DEPS_PREFIX}"'/share/pkgconfig:${PKG_CONFIG_LIBDIR}"
    export PKG_CONFIG_PATH="'"${CVC_DEPS_PREFIX}"'/lib/pkgconfig:'"${CVC_DEPS_PREFIX}"'/share/pkgconfig:${PKG_CONFIG_PATH:-}"
    export CPPFLAGS="-I'"${CVC_DEPS_PREFIX}"'/include ${CPPFLAGS:-}"
    export LDFLAGS="-L'"${CVC_DEPS_PREFIX}"'/lib ${LDFLAGS:-}"
    # -O1: emcc/clang 23 crashes with a segfault on coders/png.c at -O2
    # (default). -O1 codegen is stable; the size delta of the resulting
    # libMagickCore is ~5% — an acceptable tradeoff for actual PNG support.
    export CFLAGS="-O1 ${CFLAGS:-}"
    export CXXFLAGS="-O1 ${CXXFLAGS:-}"
    # libtiff.a references private deps (Lerc, zstd) not carried through by
    # its .pc Requires; add them explicitly for the magick CLI link step.
    # Note: the lib is libLerc.a (capital L) — matches the cvcpkg lerc bundle.
    export LIBS="-lLerc -lzstd -lsharpyuv ${LIBS:-}"
    exec ./configure "$@"
' _ \
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
    --without-freetype \
    --without-jbig \
    --without-raw \
    --without-openjp2 \
    --without-threads \
    --disable-docs

emmake make -j "${CVC_JOBS}"
emmake make install

# ImageMagick's autotools build embeds a SECOND compilation of six
# MagickCore translation units (magick.c, cache.c, tree.c, list.c, view.c,
# histogram.c) into libMagickCore-7.Q16HDRI.a — a smaller stub compiled
# from the top-level libtool convenience-lib pass, distinct from the full
# MagickCore/*_la-*.o objects. Each has its own file-scope `static
# magick_list`, so wasm-ld can end up satisfying the coder-query call
# chain from the STUB copy (its magick_list stays empty) while our
# RegisterPNGImage / RegisterStaticModules writes to the FULL copy's
# list. Result at runtime: RegisterMagickInfo returns a nonzero id but
# Magick::Image throws NoDecodeDelegateForThisImageFormat 'PNG' because
# the list the reader consults never received the entry. Strip the stub
# duplicates from the archive.
_ar="${CVC_INSTALL_DIR}/lib/libMagickCore-7.Q16HDRI.a"
if [[ -f "${_ar}" ]]; then
    for _obj in libMagickCore_7_Q16HDRI_la-magick.o \
                libMagickCore_7_Q16HDRI_la-cache.o \
                libMagickCore_7_Q16HDRI_la-tree.o \
                libMagickCore_7_Q16HDRI_la-list.o \
                libMagickCore_7_Q16HDRI_la-view.o \
                libMagickCore_7_Q16HDRI_la-histogram.o; do
        emar d "${_ar}" "${_obj}" 2>/dev/null || true
    done
    emranlib "${_ar}"
fi

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
