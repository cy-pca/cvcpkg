#!/usr/bin/env bash
# recipes/gtk4/build.sh — build GTK 4 from source with Meson on POSIX.
#
# Requires the following system packages (via pkg-config):
#   glib-2.0, cairo, cairo-gobject, pango, pangocairo, harfbuzz,
#   gdk-pixbuf-2.0, epoxy, wayland-client (Linux), fribidi
#
# On Debian/Ubuntu:
#   apt install libglib2.0-dev libcairo2-dev libpango1.0-dev \
#               libharfbuzz-dev libgdk-pixbuf-2.0-dev libepoxy-dev \
#               libfribidi-dev libwayland-dev wayland-protocols \
#               libxkbcommon-dev
# On macOS:
#   brew install glib cairo pango harfbuzz gdk-pixbuf libepoxy \
#                fribidi pkg-config
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

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

# Meson build.  Wayland-only on Linux keeps the surface small; add
# -Dx11-backend=true for X11 support.
MESON_OPTS=(
    --prefix="${CVC_INSTALL_DIR}"
    --buildtype=release
    -Dbuild-tests=false
    -Dbuild-examples=false
    -Dbuild-demos=false
    -Dbuild-testsuite=false
    -Dintrospection=disabled
    -Ddocumentation=false
    -Dman-pages=false
    -Dprint-backends=file
)

case "${CVC_PLATFORM}" in
    linux)
        MESON_OPTS+=( -Dwayland-backend=true -Dx11-backend=true )
        ;;
    macos)
        MESON_OPTS+=( -Dmacos-backend=true -Dwayland-backend=false -Dx11-backend=false )
        ;;
esac

meson setup "${CVC_BUILD_DIR}/meson" "${SRC}" "${MESON_OPTS[@]}"
meson compile -C "${CVC_BUILD_DIR}/meson" -j "${CVC_JOBS}"
meson install -C "${CVC_BUILD_DIR}/meson"

echo "gtk4 ${GTK_VERSION} installed to ${CVC_INSTALL_DIR}"
