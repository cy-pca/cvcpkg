#!/usr/bin/env bash
# recipes/pcre2/build.sh — build PCRE2 from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# pcre2grep's .gz/.bz2 input support is OFF on purpose.  CMakeLists.txt runs
# find_package(ZLIB) / find_package(BZip2) and turns PCRE2_SUPPORT_LIBZ /
# PCRE2_SUPPORT_LIBBZ2 ON for whichever it finds, so pcre2grep linked any zlib
# or bzip2 that happened to be visible: cvcpkg's from a shared populate prefix
# (macOS cvc.6: @rpath/libz.1.dylib and @rpath/libbz2.1.0.dylib) or the host's
# (NetBSD: libbz2.so.1, libz.so.1), neither of them declared.  pcre2 sits low
# in many dependency graphs, so it declares no zlib/bzip2 for one niche
# pcre2grep feature; the libraries never used either.
cvc_cmake_build \
    -DPCRE2_BUILD_PCRE2_8=ON \
    -DPCRE2_BUILD_PCRE2_16=ON \
    -DPCRE2_BUILD_PCRE2_32=OFF \
    -DPCRE2_SUPPORT_UNICODE=ON \
    -DPCRE2_BUILD_PCRE2GREP=ON \
    -DPCRE2_SUPPORT_LIBZ=OFF \
    -DPCRE2_SUPPORT_LIBBZ2=OFF \
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
