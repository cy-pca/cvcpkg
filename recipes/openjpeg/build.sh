#!/usr/bin/env bash
# recipes/openjpeg/build.sh — build libopenjp2 (shared, no codec executables).
#
# BUILD_CODEC would build opj_compress/opj_decompress against whatever
# libtiff/libpng/lcms2 the builder has; the library itself needs none of them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_STATIC_LIBS=OFF \
    -DBUILD_CODEC=OFF \
    -DBUILD_THIRDPARTY=OFF \
    -DBUILD_TESTING=OFF \
    -DBUILD_DOC=OFF
