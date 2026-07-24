#!/usr/bin/env bash
# recipes/libXau/build.sh — build libXau 1.0.12 with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

# OpenBSD: libXau's meson probes gethostbyname_r via `cc.find_library('nls')`
# (required) only when the plain gethostbyname_r function isn't found — which is
# the case on OpenBSD, and there is no 'nls' library, so the configure hard-fails
# ("C shared or static library 'nls' not found"). Disabling xthreads skips that
# whole MT-safe block; libXau's thread guards aren't needed by our consumers.
_extra=()
[[ "${CVC_PLATFORM}" == "openbsd" ]] && _extra+=(-Dxthreads=false)

cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig,${CVC_DEPS_PREFIX}/libdata/pkgconfig,${CVC_DEPS_PREFIX}/share/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN" \
    "${_extra[@]}"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
