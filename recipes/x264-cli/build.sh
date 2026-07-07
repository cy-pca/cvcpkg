#!/usr/bin/env bash
# recipes/x264-cli/build.sh — build the x264 H.264 encoder CLI tool.
#
# Built from the same source tarball as the x264 library recipe.
# x264 (libx264) must be pre-built and available in CVC_DEPS_PREFIX.
# Only the x264 encoder binary is installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-cli \
    --disable-static \
    --disable-shared \
    --disable-lavf \
    --disable-swscale \
    --disable-opencl \
    --sysroot=/ \
    --extra-cflags="${CPPFLAGS:-}" \
    --extra-ldflags="${LDFLAGS:-}"

make -j "${CVC_JOBS}" x264

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 x264 "${CVC_INSTALL_DIR}/bin/x264"
