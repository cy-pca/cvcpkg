#!/usr/bin/env bash
# recipes/lame/build.sh — build the lame MP3 encoder CLI tool.
#
# The lame binary is built from the same source tarball as libmp3lame.
# libmp3lame must be pre-built and available in CVC_DEPS_PREFIX.
# Only the lame frontend binary is installed; the library itself is
# provided by the libmp3lame recipe.
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
    --disable-dependency-tracking \
    --enable-frontend \
    --disable-shared \
    --disable-static \
    --disable-nls

# Build only the frontend directory.
make -j "${CVC_JOBS}" -C frontend

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 frontend/lame "${CVC_INSTALL_DIR}/bin/lame"
