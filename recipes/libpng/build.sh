#!/usr/bin/env bash
# recipes/libpng/build.sh — build libpng from source with CMake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# (no-op touch: re-push to embed the current recipes/_common/env-openbsd.sh,
#  which now exports LD_LIBRARY_PATH so cmake, invoked here as a build-tool
#  dependency, can resolve its own runtime deps like libcurl.so on OpenBSD.)

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DPNG_TESTS=OFF \
    -DPNG_TOOLS=OFF \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}"
