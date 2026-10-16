#!/usr/bin/env bash
# recipes/cairo/build.sh — build Cairo with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
# share/pkgconfig too: xorgproto installs xproto.pc and kbproto.pc there, and
# x11.pc requires both (the Xlib surface below). libdata/pkgconfig is FreeBSD's.
_pc_dirs=()
for _p in "${CVC_DEPS_PREFIX}" ${CVC_BUILD_PREFIX:+"${CVC_BUILD_PREFIX}"}; do
    _pc_dirs+=("${_p}/lib/pkgconfig" "${_p}/libdata/pkgconfig" "${_p}/share/pkgconfig")
done
_pc_path="$(IFS=:; echo "${_pc_dirs[*]}")"
export PKG_CONFIG_PATH="${_pc_path}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _default_lib=static
fi

if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN"
fi

cd "${CVC_SOURCE_DIR}"

MESON_OPTS=(
    --prefix="${CVC_INSTALL_DIR}"
    --wrap-mode=nofallback
    --buildtype=release
    --libdir=lib
    --default-library="${_default_lib}"
    --pkg-config-path="$(IFS=,; echo "${_pc_dirs[*]}")"
    -Dc_link_args="${_rpath_flags}"
    -Dcpp_link_args="${_rpath_flags}"
    -Dfreetype=enabled
    -Dfontconfig=enabled
    -Dpng=enabled
    -Dglib=enabled
    -Dtests=disabled
    -Dspectre=disabled
    -Dsymbol-lookup=disabled
)

# The Xlib surface (cairo-xlib, cairo-xlib-xrender) on Linux and the BSDs,
# from cvcpkg's libx11/libxext/libxrender: gtk4's X11 backend requires it.
# No XCB surface: nothing in the catalog needs cairo-xcb.
case "${CVC_PLATFORM}" in
    macos)
        MESON_OPTS+=( -Dquartz=enabled -Dxcb=disabled -Dxlib=disabled )
        ;;
    *)
        MESON_OPTS+=( -Dxcb=disabled -Dxlib=enabled )
        ;;
esac

meson setup "${CVC_BUILD_DIR}" "${MESON_OPTS[@]}"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
