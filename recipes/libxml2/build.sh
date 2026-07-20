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
cvc_cmake_build \
    -DBUILD_SHARED_LIBS=ON \
    -DLIBXML2_WITH_ZLIB=ON \
    -DLIBXML2_WITH_LZMA=OFF \
    -DLIBXML2_WITH_ICONV=OFF \
    -DLIBXML2_WITH_ICU=OFF \
    -DLIBXML2_WITH_PYTHON=OFF \
    -DLIBXML2_WITH_PROGRAMS=OFF \
    -DLIBXML2_WITH_TESTS=OFF
