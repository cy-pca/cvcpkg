#!/usr/bin/env bash
# recipes/libvorbis/build-wasm.sh — cross-compile libvorbis to wasm via Emscripten.
#
# libvorbis is floating-point DSP over libogg: MDCT, codebooks and psychoacoustics,
# all portable C with no OS surface.  The one part that touches the platform is
# libvorbisfile's ov_open(FILE *), and stdio is exactly what Emscripten provides.
# libogg already covers wasm, which is what makes this entry legal at all —
# libvorbis runtime-depends on it, so the closure check would reject a wasm
# claim here otherwise.
#
# No SIMD switch is needed: libvorbis has no runtime CPU dispatch and no
# assembly; its only arch-specific code is a compile-time selection between the
# floating-point and integer lrint paths, chosen by configure from headers.
#
# Autotools shape rather than cvc_cmake_build, matching the native build.sh and
# keeping the installed layout identical across platforms.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-unknown-emscripten \
    --build="${BUILD_TRIPLET}" \
    --with-ogg="${CVC_DEPS_PREFIX}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
