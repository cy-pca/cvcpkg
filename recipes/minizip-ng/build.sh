#!/usr/bin/env bash
# recipes/minizip-ng/build.sh — build minizip-ng (zlib only, no compat layer).
#
# Everything minizip-ng would otherwise detect or fetch is switched off
# explicitly (MZ_FETCH_LIBS would download zlib-ng/bzip2/xz/zstd), so the
# result does not depend on what the builder has installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DMZ_COMPAT=OFF \
    -DMZ_ZLIB=ON \
    -DMZ_BZIP2=OFF \
    -DMZ_LZMA=OFF \
    -DMZ_ZSTD=OFF \
    -DMZ_LIBCOMP=OFF \
    -DMZ_FETCH_LIBS=OFF \
    -DMZ_FORCE_FETCH_LIBS=OFF \
    -DMZ_PKCRYPT=OFF \
    -DMZ_WZAES=OFF \
    -DMZ_OPENSSL=OFF \
    -DMZ_LIBBSD=OFF \
    -DMZ_ICONV=OFF \
    -DMZ_BUILD_TESTS=OFF \
    -DMZ_BUILD_UNIT_TESTS=OFF
