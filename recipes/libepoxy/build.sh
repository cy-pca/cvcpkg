#!/usr/bin/env bash
# recipes/libepoxy/build.sh — build libepoxy with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

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
    -Dc_link_args="${_rpath_flags}"
    -Dtests=false
    -Ddocs=false
)

case "${CVC_PLATFORM}" in
    linux)
        # EGL's headers (and libEGL, which epoxy dlopens) come from cvcpkg's
        # libglvnd.  Nothing pointed meson there, so the build used the build
        # host's /usr/include/EGL and failed on runners without it
        # (linux/arm64: "EGL/eglplatform.h: No such file or directory").
        export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
        export CFLAGS="-I${CVC_DEPS_PREFIX}/include ${CFLAGS:-}"
        MESON_OPTS+=( -Degl=yes -Dglx=no -Dx11=false )
        ;;
    macos)
        MESON_OPTS+=( -Degl=no -Dglx=no -Dx11=false )
        ;;
    *)
        MESON_OPTS+=( -Degl=no -Dglx=no -Dx11=false )
        ;;
esac

meson setup "${CVC_BUILD_DIR}" "${MESON_OPTS[@]}"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
