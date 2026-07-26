#!/usr/bin/env bash
# recipes/flac/build.sh — build FLAC (libFLAC + libFLAC++) from source with CMake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# FLAC finds libogg via pkg-config (cmake/FindOgg.cmake -> pkg_check_modules).
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

# BUILD_SHARED_LIBS is set from CVC_LINK by cvc_cmake_build; FLAC honors it.
# BUILD_CXXLIBS defaults ON, so libFLAC++ is produced in both link modes.
cvc_cmake_build \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DWITH_OGG=ON \
    -DBUILD_TESTING=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_DOCS=OFF \
    -DBUILD_PROGRAMS=OFF \
    -DINSTALL_MANPAGES=OFF
