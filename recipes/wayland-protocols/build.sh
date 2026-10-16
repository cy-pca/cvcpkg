#!/usr/bin/env bash
# recipes/wayland-protocols/build.sh — install Wayland protocol XML files
# with Meson (Linux/FreeBSD only).  This is a data-only package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# The deps and the build prefix both: wayland (wayland-scanner) is a build
# dependency, installed into the build prefix, and building gtk4's closure from
# source failed with "Build-time dependency wayland-scanner found: NO".
# share/pkgconfig too (wayland-protocols' .pc lives there); libdata/pkgconfig
# is FreeBSD's.
export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
_pc_dirs=()
for _p in "${CVC_DEPS_PREFIX}" ${CVC_BUILD_PREFIX:+"${CVC_BUILD_PREFIX}"}; do
    _pc_dirs+=("${_p}/lib/pkgconfig" "${_p}/libdata/pkgconfig" "${_p}/share/pkgconfig")
done
_pc_path="$(IFS=:; echo "${_pc_dirs[*]}")"
export PKG_CONFIG_PATH="${_pc_path}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --pkg-config-path="$(IFS=,; echo "${_pc_dirs[*]}")" \
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
