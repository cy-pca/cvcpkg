#!/usr/bin/env bash
# recipes/xkbcommon-x11/build.sh — build libxkbcommon with X11 (xcb) support and
# ship only the libxkbcommon-x11 delta (package.files filters); the core
# libxkbcommon comes from the base `xkbcommon` recipe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

cd "${CVC_SOURCE_DIR}"

# enable-x11=true builds libxkbcommon-x11 (needs xcb + xcb-xkb from libxcb).
# wayland/tools/docs/xkbregistry off — we only want the x11 helper; xkbregistry
# would pull an unpackaged libxml-2.0 (see the base xkbcommon recipe).
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig,${CVC_DEPS_PREFIX}/libdata/pkgconfig,${CVC_DEPS_PREFIX}/share/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN" \
    -Denable-x11=true \
    -Denable-wayland=false \
    -Denable-docs=false \
    -Denable-tools=false \
    -Denable-xkbregistry=false

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
