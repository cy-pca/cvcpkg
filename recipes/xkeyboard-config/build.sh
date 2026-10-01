#!/usr/bin/env bash
# recipes/xkeyboard-config/build.sh — install xkeyboard-config 2.48 XKB data (Meson).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

cd "${CVC_SOURCE_DIR}"
# -Dnls=false: translations need gettext's msgfmt; the XKB data itself is
# language-neutral and the X server never reads the .mo files.
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    -Dnls=false
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# Meson installs the legacy share/X11/xkb entry as a symlink whose target is the
# ABSOLUTE (temporary) install prefix.  Make it relative so the data tree
# survives relocation (and bundle extraction, which rejects absolute links).
_xkb_link="${CVC_INSTALL_DIR}/share/X11/xkb"
_xkb_target="$(ls -d "${CVC_INSTALL_DIR}"/share/xkeyboard-config-* | head -n 1)"
rm -f "${_xkb_link}"
ln -s "../$(basename "${_xkb_target}")" "${_xkb_link}"

cvc_rewrite_install_paths
