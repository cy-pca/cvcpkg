#!/usr/bin/env bash
# recipes/xkbcommon/build.sh — build libxkbcommon with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _default_lib=static
fi

_rpath_flags="-Wl,-rpath,\$ORIGIN"

cd "${CVC_SOURCE_DIR}"

# enable-xkbregistry defaults to true and makes meson.build:360 require
# libxml-2.0 unconditionally (no fallback), which there is no recipe for -- so
# the build died with 'Dependency "libxml-2.0" not found'.  libxkbregistry only
# enumerates available layouts from evdev.xml for config UIs; gtk4 is our sole
# dependent and its source references neither xkbregistry nor rxkb_*, using only
# core <xkbcommon/xkbcommon.h>.  Turning it off drops the libxml2 requirement
# rather than adding a package nothing links.
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="${_rpath_flags}" \
    -Denable-wayland=true \
    -Denable-x11=false \
    -Denable-docs=false \
    -Denable-tools=false \
    -Denable-xkbregistry=false

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
