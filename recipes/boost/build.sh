#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Boost's CMake build (>= 1.82) supports standard CMake workflow.
#
# Optional backends are pinned instead of left to find_package(): each one
# defaults ON when the build host happens to have the library, which made the
# bundle depend on whatever that host had installed rather than on what this
# recipe declares. 1.86.0+cvc.4 shipped libboost_locale NEEDing the linux
# builder's ICU 70 (libicui18n.so.70 / libicuuc.so.70) and, on macOS,
# libboost_iostreams loading /opt/homebrew/opt/zstd/lib/libzstd.1.dylib plus the
# bzip2/xz dylibs pack-all had left in its shared prefix. cvcpkg has no ICU
# recipe, so Boost.Locale uses its iconv/POSIX/std backends; Boost.Iostreams
# keeps only zlib, the runtime dependency the recipe declares. That matches what
# every linux bundle already provided, on every platform.
cvc_cmake_build \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system \
    -DBOOST_LOCALE_ENABLE_ICU=OFF \
    -DBOOST_IOSTREAMS_ENABLE_ZLIB=ON \
    -DBOOST_IOSTREAMS_ENABLE_BZIP2=OFF \
    -DBOOST_IOSTREAMS_ENABLE_LZMA=OFF \
    -DBOOST_IOSTREAMS_ENABLE_ZSTD=OFF
