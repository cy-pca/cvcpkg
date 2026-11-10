#!/usr/bin/env bash
# recipes/glfw/build.sh — build GLFW with CMake (static or shared per CVC_LINK).
#
# On Linux both the X11 and Wayland backends are built; the X11 libs,
# xkbcommon, wayland-client and wayland-scanner/wayland-protocols come from
# the cvcpkg dependency prefix.  The BSDs build X11 only (see below).  On macOS
# the Cocoa backend is used and GLFW forces the X11/Wayland options OFF on
# Apple, so we don't pass them there.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Make cvcpkg deps discoverable: wayland-scanner (PATH), X11/wayland .pc
# files (PKG_CONFIG_PATH) and FindX11 search roots (CMAKE_PREFIX_PATH).
export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
# FindX11 uses find_path/find_library, which on Unix reach the deps prefix only
# through CMAKE_PREFIX_PATH.  The shared env exports it ';'-joined
# ("<deps>;<build>"), but CMake splits the environment variable on ':', so with
# a separate build prefix (`cvcpkg build --with-deps`) it named no directory:
# FindX11 took the host's /usr/include and failed on hosts without libXrandr
# headers ("RandR headers not found", GitHub's ubuntu runners).  Prepend the
# deps prefix ':'-separated, as sdl2 and sdl3 do.
export CMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"

# Translate CVC_LINK into GLFW's own static/shared switch (overrides
# BUILD_SHARED_LIBS, which cvc_cmake_build also sets from CVC_LINK).
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _glfw_libtype=STATIC
else
    _glfw_libtype=SHARED
fi

_glfw_opts=(
    -DGLFW_LIBRARY_TYPE="${_glfw_libtype}"
    -DGLFW_BUILD_EXAMPLES=OFF
    -DGLFW_BUILD_TESTS=OFF
    -DGLFW_BUILD_DOCS=OFF
)

case "${CVC_PLATFORM}" in
    linux)
        _glfw_opts+=( -DGLFW_BUILD_X11=ON -DGLFW_BUILD_WAYLAND=ON )
        ;;
    freebsd|openbsd|netbsd)
        # X11 only.  Wayland is not a first-class platform on OpenBSD/NetBSD,
        # and on FreeBSD GLFW's Wayland backend includes <linux/input.h>
        # (wl_init.c), which there comes only from the evdev-proto port, not
        # from cvcpkg: the dev cluster's FreeBSD build stopped on it (PR #599).
        # The FreeBSD row had never built, so no published bundle changes.
        _glfw_opts+=( -DGLFW_BUILD_X11=ON -DGLFW_BUILD_WAYLAND=OFF )
        ;;
    macos)
        # Cocoa backend; GLFW forces X11/Wayland OFF on Apple.
        ;;
esac

cvc_cmake_build "${_glfw_opts[@]}"
