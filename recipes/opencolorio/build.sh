#!/usr/bin/env bash
# recipes/opencolorio/build.sh — build the OpenColorIO library.
#
# OCIO_INSTALL_EXT_PACKAGES=NONE makes a missing dependency a configure error
# instead of a download. On arm64 OCIO's SIMD path needs the sse2neon header,
# which it would also download, so SIMD is off there (x86_64 keeps SSE/AVX).
# ZLIB_ROOT: with the dependencies split across a deps and a build prefix
# (`cvcpkg build --with-deps`), OCIO's find_package(ZLIB 1.2.13) missed the
# prefix's zlib and rejected the host's 1.2.11; point it at the deps prefix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

simd=()
case "$(uname -m)" in
    arm64|aarch64) simd=(-DOCIO_USE_SIMD=OFF -DOCIO_USE_SSE2NEON=OFF) ;;
    *)             simd=(-DOCIO_USE_SIMD=ON) ;;
esac

cvc_cmake_build \
    -DOCIO_BUILD_APPS=OFF \
    -DOCIO_BUILD_PYTHON=OFF \
    -DOCIO_BUILD_NUKE=OFF \
    -DOCIO_BUILD_JAVA=OFF \
    -DOCIO_BUILD_OPENFX=OFF \
    -DOCIO_BUILD_DOCS=OFF \
    -DOCIO_BUILD_TESTS=OFF \
    -DOCIO_BUILD_GPU_TESTS=OFF \
    -DOCIO_USE_OIIO_FOR_APPS=OFF \
    -DOCIO_INSTALL_EXT_PACKAGES=NONE \
    -DZLIB_ROOT="${CVC_DEPS_PREFIX}" \
    ${simd[@]+"${simd[@]}"}
