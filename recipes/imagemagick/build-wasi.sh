#!/usr/bin/env bash
# recipes/imagemagick/build-wasi.sh — cross-compile ImageMagick to wasm32-wasi via wasi-sdk.
#
# The wasm build's feature set (build-wasm.sh): Q16-HDRI, Magick++, static
# coders, no X11, no threads, no modules, and the codec delegates from the
# deps prefix -- zlib, bzip2, xz, zstd, jpeg, png, webp, tiff (with Lerc) and
# libxml2.  This recipe's runtime deps are exactly those.  An earlier script
# configured --without-jpeg/png/webp, which gave a MagickCore no consumer
# (libcvc's cvc::image) could read a picture with.
#
# libpng's and libjpeg's error handling is setjmp/longjmp, which env-wasi.sh's
# wasm exception-handling flags provide; CFLAGS/CXXFLAGS/LDFLAGS carry them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

# pkg-config must see the wasi deps only, never the build host's .pc files: a
# host libpng.pc found through the default search path would point configure
# at /usr/include.
export PKG_CONFIG_LIBDIR="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_LIBDIR}"

# wasip1 has no processes, users or signals.  MagickCore is a POSIX program, so:
#   - wasi-compat/ supplies the two headers wasi-libc leaves out, <sys/wait.h>
#     and <pwd.h> (no child to wait for, no user to look up), and
#     cvc-wasi-compat.h, force-included, defines system() to fail (ENOSYS);
#   - wasi-libc's emulations supply signal()/raise() (handlers that only
#     raise() can reach), getpid() and times() (wall clock).
# The emulation libraries go into LIBS, so MagickCore.pc's Libs.private
# names them for a static consumer; the compat headers are not installed.
# _GNU_SOURCE: configure finds strtod_l() in libc.a, and wasi-libc declares it
# (with strtof_l/strtold_l) only for _GNU_SOURCE, as glibc does.
_compat="${SCRIPT_DIR}/wasi-compat"
_emulated="-D_GNU_SOURCE -D_WASI_EMULATED_SIGNAL -D_WASI_EMULATED_GETPID -D_WASI_EMULATED_PROCESS_CLOCKS"

WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${_WASI_SYSROOT}"
export CPPFLAGS="-I${_compat} -include ${_compat}/cvc-wasi-compat.h ${_emulated} -I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export CFLAGS="${WASI_TARGET_FLAGS} ${CFLAGS:-}"
export CXXFLAGS="${WASI_TARGET_FLAGS} ${CXXFLAGS:-}"
export LDFLAGS="${WASI_TARGET_FLAGS} -L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
# libtiff.a references private deps (Lerc, zstd) its .pc Requires do not carry,
# and libwebp.a needs libsharpyuv; name them for the magick CLI link, as on wasm.
export LIBS="-lLerc -lzstd -lsharpyuv -lwasi-emulated-signal -lwasi-emulated-getpid -lwasi-emulated-process-clocks ${LIBS:-}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-wasip1 \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --with-quantum-depth=16 \
    --enable-hdri \
    --with-magick-plus-plus \
    --without-perl \
    --without-x \
    --with-zlib \
    --with-bzlib \
    --with-lzma \
    --with-zstd \
    --with-jpeg \
    --with-png \
    --with-webp \
    --with-tiff \
    --with-xml \
    --without-freetype \
    --without-fontconfig \
    --without-jbig \
    --without-raw \
    --without-openjp2 \
    --without-threads \
    --without-openmp \
    --without-modules \
    --disable-docs

make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
