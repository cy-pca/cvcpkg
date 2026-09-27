#!/usr/bin/env bash
# recipes/wayland/build.sh — build Wayland with Meson (Linux/FreeBSD only).
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

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="${_rpath_flags}" \
    -Dtests=false \
    -Ddocumentation=false \
    `# DTD validation pulls libxml-2.0 (src/meson.build gates dependency('libxml-2.0')` \
    `# behind get_option('dtd_validation'), which defaults ON). libxml2 is not in the` \
    `# dep closure, so a from-source build fails at configure: "Dependency libxml-2.0` \
    `# not found". DTD validation only checks protocol XML at authoring time; libwayland` \
    `# and wayland-scanner's codegen for consumers (SDL/GTK) don't need it. Disable it` \
    `# to keep wayland dependency-free across linux/freebsd/openbsd/netbsd.` \
    -Ddtd_validation=false

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
