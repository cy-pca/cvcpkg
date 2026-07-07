#!/usr/bin/env bash
# recipes/gtk4/build.sh — build GTK 4 from source with Meson on POSIX.
#
# All dependencies (glib, cairo, pango, harfbuzz, gdk-pixbuf, epoxy,
# fribidi, graphene, wayland, xkbcommon) are resolved from the cvcpkg
# dependency prefix via PKG_CONFIG_PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

GTK_VERSION="4.16.7"
GTK_TARBALL="gtk-${GTK_VERSION}.tar.xz"
GTK_URL="https://download.gnome.org/sources/gtk/4.16/${GTK_TARBALL}"

# Fetch tarball if a source dir isn't already unpacked.
SRC="${CVC_SOURCE_DIR}"
if [[ ! -f "${SRC}/meson.build" ]]; then
    echo "Downloading ${GTK_URL} ..."
    curl -fSL -o "${CVC_BUILD_DIR}/${GTK_TARBALL}" "${GTK_URL}"
    mkdir -p "${SRC}"
    tar xf "${CVC_BUILD_DIR}/${GTK_TARBALL}" -C "${SRC}" --strip-components=1
fi

if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN"
fi

# Meson build.  Wayland + X11 on Linux; macOS native backend.
MESON_OPTS=(
    --prefix="${CVC_INSTALL_DIR}"
    --buildtype=release
    --libdir=lib
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig"
    -Dc_link_args="${_rpath_flags}"
    -Dcpp_link_args="${_rpath_flags}"
    -Dbuild-tests=false
    -Dbuild-examples=false
    -Dbuild-demos=false
    -Dbuild-testsuite=false
    -Dintrospection=disabled
    -Ddocumentation=false
    -Dman-pages=false
)

# GTK 4.16 removed the combined `-Dprint-backends` string option in favour of
# individual feature options.  The built-in "file" print backend is always
# compiled, so we simply disable the optional CUPS/CPDB backends to keep the
# build lean and dependency-free across platforms.
MESON_OPTS+=( -Dprint-cups=disabled -Dprint-cpdb=disabled )

case "${CVC_PLATFORM}" in
    linux)
        MESON_OPTS+=( -Dwayland-backend=true -Dx11-backend=true )
        ;;
    macos)
        MESON_OPTS+=( -Dmacos-backend=true -Dwayland-backend=false -Dx11-backend=false )
        ;;
    freebsd|openbsd|netbsd)
        # BSDs use X11; Wayland support is still maturing on non-Linux BSDs.
        MESON_OPTS+=( -Dx11-backend=true -Dwayland-backend=false )
        ;;
esac

meson setup "${CVC_BUILD_DIR}/meson" "${SRC}" "${MESON_OPTS[@]}"
meson compile -C "${CVC_BUILD_DIR}/meson" -j "${CVC_JOBS}"
meson install -C "${CVC_BUILD_DIR}/meson"

cvc_rewrite_install_paths

echo "gtk4 ${GTK_VERSION} installed to ${CVC_INSTALL_DIR}"
