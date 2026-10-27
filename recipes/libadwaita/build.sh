#!/usr/bin/env bash
# recipes/libadwaita/build.sh — build libadwaita from source with Meson.
#
# GTK 4, GLib and FriBidi resolve from the cvcpkg dependency prefix via
# PKG_CONFIG_PATH; sassc, meson, ninja and python3 from the build prefix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# The build prefix first: its python3 runs gen-public-types.py and glib-mkenums,
# and its sassc compiles the stylesheet.
export PATH="${CVC_BUILD_PREFIX:+${CVC_BUILD_PREFIX}/bin:}${CVC_DEPS_PREFIX}/bin:${PATH}"
# share/pkgconfig too: xorgproto and wayland-protocols, which gtk4's .pc files
# pull in, install their .pc files there.
_pc_dirs=()
for _p in "${CVC_DEPS_PREFIX}" ${CVC_BUILD_PREFIX:+"${CVC_BUILD_PREFIX}"}; do
    _pc_dirs+=("${_p}/lib/pkgconfig" "${_p}/share/pkgconfig")
done
_pc_path="$(IFS=:; echo "${_pc_dirs[*]}")"
export PKG_CONFIG_PATH="${_pc_path}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

command -v sassc >/dev/null || { echo "libadwaita: no sassc on PATH (build dependency)" >&2; exit 1; }

MESON_OPTS=(
    --prefix="${CVC_INSTALL_DIR}"
    --wrap-mode=nofallback
    --buildtype=release
    --libdir=lib
    --pkg-config-path="$(IFS=,; echo "${_pc_dirs[*]}")"
    -Dc_link_args="-Wl,-rpath,\$ORIGIN"
    # appstream-optional.patch: no AppStream in cvcpkg (recipe notes).
    -Dappstream=disabled
    -Dintrospection=disabled
    -Dvapi=false
    -Dgtk_doc=false
    -Dtests=false
    -Dexamples=false
)

meson setup "${CVC_BUILD_DIR}/meson" "${CVC_SOURCE_DIR}" "${MESON_OPTS[@]}"
meson compile -C "${CVC_BUILD_DIR}/meson" -j "${CVC_JOBS}"
meson install -C "${CVC_BUILD_DIR}/meson"

# The patch must really have taken AppStream out: the library may not need it.
if readelf -d "${CVC_INSTALL_DIR}/lib/libadwaita-1.so.0" | grep -q 'NEEDED.*libappstream'; then
    echo "libadwaita: links libappstream despite -Dappstream=disabled" >&2
    exit 1
fi

cvc_rewrite_install_paths

echo "libadwaita installed to ${CVC_INSTALL_DIR}"
