#!/usr/bin/env bash
# recipes/wayland/build.sh — build Wayland with Meson (Linux and the BSDs).
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

# $ORIGIN for the libraries, $ORIGIN/../lib for bin/wayland-scanner (which links
# libexpat). One link-time RUNPATH for both, set here rather than patched in
# afterwards: growing an RPATH with patchelf breaks NetBSD objects (see
# recipes/curl), and cvcpkg's packager leaves an RPATH that already starts with
# $ORIGIN and holds only $ORIGIN-relative entries exactly as it is.
_rpath_flags="-Wl,-rpath,\$ORIGIN:\$ORIGIN/../lib"

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

# meson's pkgconfig module installs .pc files to <prefix>/libdata/pkgconfig on
# FreeBSD (and <libdir>/pkgconfig everywhere else); wayland's meson.build passes
# no install_dir to override it. Keep every platform's bundle in the one layout
# package.files and consumers' PKG_CONFIG_PATH expect. Same depth below the
# prefix, so cvc_rewrite_install_paths' ${pcfiledir}/../.. anchors still hold.
if [ -d "${CVC_INSTALL_DIR}/libdata/pkgconfig" ]; then
    mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
    for _pc in "${CVC_INSTALL_DIR}"/libdata/pkgconfig/*.pc; do
        [ -e "${_pc}" ] || continue
        mv "${_pc}" "${CVC_INSTALL_DIR}/lib/pkgconfig/"
    done
    rmdir "${CVC_INSTALL_DIR}/libdata/pkgconfig"
    rmdir "${CVC_INSTALL_DIR}/libdata" 2>/dev/null || true
fi

cvc_rewrite_install_paths
