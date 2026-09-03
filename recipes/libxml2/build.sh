#!/usr/bin/env bash
# recipes/libxml2/build.sh — build libxml2 on Linux/macOS/BSD with CMake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Minimal feature set — enough for ImageMagick's config/SVG parsing while
# keeping the dependency surface small (zlib only). Optional delegates that
# would pull extra deps (lzma, iconv, icu, python) are off; the CLI tools and
# tests are not shipped.
EXTRA_CMAKE_ARGS=()

# NetBSD/OpenBSD: disable the runtime module loader (xmlmodule.c). libxml2
# enables modules by default, but on these BSDs the CMake build doesn't
# resolve the platform dlopen implementation, so libxml2.so ships with
# undefined xmlModulePlatformOpen/Symbol/Close and every consumer's link
# fails (e.g. ImageMagick: "ld: error: undefined reference due to
# --no-allow-shlib-undefined: xmlModulePlatformOpen"). ImageMagick and our
# other consumers use libxml2 only for config/SVG parsing, never runtime
# module loading, so turning modules off is safe and unblocks the link.
case "${CVC_PLATFORM}" in
    netbsd|openbsd)
        EXTRA_CMAKE_ARGS+=(-DLIBXML2_WITH_MODULES=OFF)
        ;;
esac

cvc_cmake_build \
    -DBUILD_SHARED_LIBS=ON \
    -DLIBXML2_WITH_ZLIB=ON \
    -DLIBXML2_WITH_LZMA=OFF \
    -DLIBXML2_WITH_ICONV=OFF \
    -DLIBXML2_WITH_ICU=OFF \
    -DLIBXML2_WITH_PYTHON=OFF \
    -DLIBXML2_WITH_PROGRAMS=OFF \
    -DLIBXML2_WITH_TESTS=OFF \
    "${EXTRA_CMAKE_ARGS[@]}"
