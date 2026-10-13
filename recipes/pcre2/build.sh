#!/usr/bin/env bash
# recipes/pcre2/build.sh — build PCRE2 from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DPCRE2_BUILD_PCRE2_8=ON \
    -DPCRE2_BUILD_PCRE2_16=ON \
    -DPCRE2_BUILD_PCRE2_32=OFF \
    -DPCRE2_SUPPORT_UNICODE=ON \
    -DPCRE2_BUILD_PCRE2GREP=ON \
    -DPCRE2_BUILD_TESTS=OFF \
    -DINSTALL_PKGCONFIG_DIR="${CVC_INSTALL_DIR}/lib/pkgconfig"

# PCRE2 10.44 installs its CMake package config to <prefix>/cmake/, which
# find_package() does not search on Unix (that layout is Windows-only), so
# find_package(pcre2) never found it.  The config locates pcre2 with plain
# find_path/find_library, not relative to itself, so it can simply move.
if [ -f "${CVC_INSTALL_DIR}/cmake/pcre2-config.cmake" ]; then
    mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/pcre2"
    mv "${CVC_INSTALL_DIR}"/cmake/pcre2-config*.cmake "${CVC_INSTALL_DIR}/lib/cmake/pcre2/"
    rmdir "${CVC_INSTALL_DIR}/cmake" 2>/dev/null || true
fi
