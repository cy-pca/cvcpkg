#!/usr/bin/env bash
# recipes/cairo/build.sh — build Cairo with Meson.
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

if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN"
fi

cd "${CVC_SOURCE_DIR}"

MESON_OPTS=(
    --prefix="${CVC_INSTALL_DIR}"
    --buildtype=release
    --libdir=lib
    --default-library="${_default_lib}"
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig"
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

case "${CVC_PLATFORM}" in
    linux)
        MESON_OPTS+=( -Dxcb=disabled -Dxlib=disabled )
        ;;
    macos)
        MESON_OPTS+=( -Dquartz=enabled -Dxcb=disabled -Dxlib=disabled )
        ;;
    *)
        MESON_OPTS+=( -Dxcb=disabled -Dxlib=disabled )
        ;;
esac

meson setup "${CVC_BUILD_DIR}" "${MESON_OPTS[@]}"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
