#!/usr/bin/env bash
# recipes/allegro5/build.sh — build Allegro 5 (core + addons) with CMake.
#
# The core graphics driver needs OpenGL (+ GLU) and, on X11 platforms,
# the Xlib client libraries.  Addons pull in their own deps:
#   image      → libpng, libjpeg-turbo, libwebp
#   font/ttf   → freetype
#   audio      → openal-soft   acodec → libvorbis, libogg, flac, opusfile
#   physfs     → physfs
# The Ogg video addon (Theora) is disabled — there is no theora recipe.
#
# Allegro uses its own -DSHARED switch rather than BUILD_SHARED_LIBS, so
# translate CVC_LINK into it explicitly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Make the dependency closure discoverable by find_package / pkg-config.
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
# Put the dependency lib dir on the link path. Allegro's ALSA/PulseAudio audio
# drivers link the bare names -lasound / -lpulse / -lpulse-simple, so without
# -L${prefix}/lib the hermetic builder's linker can't find them (they live in
# the cvcpkg prefix, not a system dir). CMake folds $LDFLAGS into the linker
# flags at configure time.
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,\$ORIGIN -Wl,-rpath,\$ORIGIN/../lib${LDFLAGS:+ ${LDFLAGS}}"

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _shared=OFF
else
    _shared=ON
fi

_opts=(
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}"
    -DSHARED="${_shared}"
    -DWANT_TESTS=OFF
    -DWANT_EXAMPLES=OFF
    -DWANT_DEMO=OFF
    -DWANT_DOCS=OFF
    -DWANT_DOCS_HTML=OFF
    -DWANT_DOCS_MAN=OFF
    -DWANT_VIDEO=OFF
)

# X11 client libs exist only on Linux/BSD; on macOS Allegro uses the
# Cocoa backend and the system OpenGL framework.  XF86VidMode and
# XScreenSaver are disabled (those extension libs are not in the closure).
case "${CVC_PLATFORM}" in
    linux|freebsd|openbsd|netbsd)
        _opts+=(
            -DWANT_X11=ON
            -DWANT_X11_XF86VIDMODE=OFF
            -DWANT_X11_XSCREENSAVER=OFF
        )
        ;;
esac

cvc_cmake_build "${_opts[@]}"
