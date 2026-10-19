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
# The RUNPATH anchor is $ORIGIN on ELF and @loader_path on Mach-O: ld64 takes
# no $ORIGIN, and the shell CMake's link rule runs in expands it to nothing,
# leaving `-rpath /../lib`, which ld64 rejects ("file cannot be open()ed"), so
# even CMake's compiler check failed on macOS.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _origin="@loader_path"
else
    _origin='$ORIGIN'
fi
# OpenBSD gets no RUNPATH flags: its ld.so does not expand $ORIGIN (cvcpkg
# drops RPATH there), and the shell expanding $ORIGIN to nothing left
# `-Wl,-rpath, -Wl,-rpath,/../lib`, which OpenBSD's toolchain reads as an input
# file "/../lib" ("ld: error: cannot open /../lib"), failing even CMake's
# compiler check (populate 2026-10-19).
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib${LDFLAGS:+ ${LDFLAGS}}"
else
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,${_origin} -Wl,-rpath,${_origin}/../lib${LDFLAGS:+ ${LDFLAGS}}"
fi

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

# allegro's exported targets list the SOURCE tree's include dir next to the
# installed one (INTERFACE_INCLUDE_DIRECTORIES "${_IMPORT_PREFIX}/include;<src>/"),
# a build-machine path no consumer has.  cvc_rewrite_install_paths only maps
# the install and deps prefixes, so drop the source entry here.
for _f in "${CVC_INSTALL_DIR}"/lib/cmake/allegro/*.cmake; do
    [ -f "${_f}" ] || continue
    sed -i.bak -e "s#;${CVC_SOURCE_DIR%/}/\"#\"#g" -e "s#;${CVC_SOURCE_DIR%/}\"#\"#g" "${_f}"
    rm -f "${_f}.bak"
done
