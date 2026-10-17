#!/usr/bin/env bash
# recipes/libogg/build.sh — build libogg from source using autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

# Strip libtool archives (.la), as recipes/libx11 does: they record this job's
# scratch prefix (libdir), which is gone once
# cvcpkg relocates the bundle, and a downstream libtool link follows them:
# libvorbis's own .la pointed at libogg.la the same way.
# The shared library and the pkg-config .pc carry the real link information.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
