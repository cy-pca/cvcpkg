#!/usr/bin/env bash
# recipes/xorgproto/build.sh — build xorgproto 2025.1 with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig,${CVC_DEPS_PREFIX}/libdata/pkgconfig,${CVC_DEPS_PREFIX}/share/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# The .pc files go to lib/pkgconfig, where cvcpkg consumers look (as
# wayland-protocols does).  meson puts them in share/pkgconfig, an
# arch-independent package's place, and a consumer searching lib/pkgconfig only
# could not resolve x11.pc's "Requires: xproto kbproto": pango's meson then fell
# back to downloading and building its own cairo over cvcpkg's.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
mv "${CVC_INSTALL_DIR}"/share/pkgconfig/*.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/"
rmdir "${CVC_INSTALL_DIR}/share/pkgconfig"

cvc_rewrite_install_paths
