#!/usr/bin/env bash
# recipes/wayland-protocols/build.sh — install Wayland protocol XML files
# with Meson (Linux/FreeBSD only).  This is a data-only package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dtests=false

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# meson installs the .pc into share/pkgconfig (the package is arch-independent),
# but cvcpkg consumers search <prefix>/lib/pkgconfig, and package.files declares
# it there; strict globs refused cvc.2 for the mismatch.  Move it.  The rewrite
# below makes its prefix ${pcfiledir}-relative, two levels up either way.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
mv "${CVC_INSTALL_DIR}/share/pkgconfig/wayland-protocols.pc" "${CVC_INSTALL_DIR}/lib/pkgconfig/"
rmdir "${CVC_INSTALL_DIR}/share/pkgconfig" 2>/dev/null || true

cvc_rewrite_install_paths
