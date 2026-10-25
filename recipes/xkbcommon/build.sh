#!/usr/bin/env bash
# recipes/xkbcommon/build.sh — build libxkbcommon with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# The deps and the build prefix both: wayland and wayland-protocols are build
# dependencies, installed into the build prefix, where the deps-prefix-only
# search never looked.
# share/pkgconfig too (wayland-protocols' .pc lives there); libdata/pkgconfig
# is FreeBSD's.
export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
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

_rpath_flags="-Wl,-rpath,\$ORIGIN"

# Configure-time XKB config root, XKB extra path and X locale dir.  The
# patched libxkbcommon (relocatable-default-paths.patch) first tries
# <libdir>/../share/X11/xkb, ../etc/xkb and ../share/X11/locale, so these are
# only the fallback for a prefix without xkeyboard-config / libx11 data.  Left
# unset, meson bakes in ${CVC_INSTALL_DIR}/share/X11/..., the builder's job
# directory, which exists on no machine the bundle is installed on; point them
# at where each OS keeps its own X data instead, as its own libxkbcommon does.
case "${CVC_PLATFORM}" in
    freebsd)
        _xkb_root=/usr/local/share/X11/xkb
        _xkb_extra=/usr/local/etc/xkb
        _xlocale=/usr/local/lib/X11/locale
        ;;
    openbsd)
        _xkb_root=/usr/X11R6/share/X11/xkb
        _xkb_extra=/etc/xkb
        _xlocale=/usr/X11R6/share/X11/locale
        ;;
    netbsd)
        _xkb_root=/usr/X11R7/lib/X11/xkb
        _xkb_extra=/etc/xkb
        _xlocale=/usr/X11R7/lib/X11/locale
        ;;
    *)
        _xkb_root=/usr/share/X11/xkb
        _xkb_extra=/etc/xkb
        _xlocale=/usr/share/X11/locale
        ;;
esac

cd "${CVC_SOURCE_DIR}"

# enable-xkbregistry defaults to true and makes meson.build:360 require
# libxml-2.0 unconditionally (no fallback), which there is no recipe for -- so
# the build died with 'Dependency "libxml-2.0" not found'.  libxkbregistry only
# enumerates available layouts from evdev.xml for config UIs; gtk4 is our sole
# dependent and its source references neither xkbregistry nor rxkb_*, using only
# core <xkbcommon/xkbcommon.h>.  Turning it off drops the libxml2 requirement
# rather than adding a package nothing links.
meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="$(IFS=,; echo "${_pc_dirs[*]}")" \
    -Dc_link_args="${_rpath_flags}" \
    -Dxkb-config-root="${_xkb_root}" \
    -Dxkb-config-extra-path="${_xkb_extra}" \
    -Dx-locale-root="${_xlocale}" \
    -Denable-wayland=true \
    -Denable-x11=false \
    -Denable-docs=false \
    -Denable-tools=false \
    -Denable-xkbregistry=false

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
