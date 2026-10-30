#!/usr/bin/env bash
# recipes/libdeflate/build.sh — build libdeflate as a shared library.
#
# Only the shared library: OpenEXR's CMake prefers libdeflate::libdeflate_shared
# when both exist, and a static copy beside it would only invite a second
# libdeflate into a process.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DLIBDEFLATE_BUILD_SHARED_LIB=ON \
    -DLIBDEFLATE_BUILD_STATIC_LIB=OFF \
    -DLIBDEFLATE_BUILD_GZIP=OFF \
    -DLIBDEFLATE_BUILD_TESTS=OFF
