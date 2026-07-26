#!/usr/bin/env bash
# recipes/glfw/build.sh — build GLFW with CMake (static or shared per CVC_LINK).
#
# On Linux/BSD both the X11 and Wayland backends are built; the X11 libs,
# xkbcommon, wayland-client and wayland-scanner/wayland-protocols come from
# the cvcpkg dependency prefix.  On macOS the Cocoa backend is used and GLFW
# forces the X11/Wayland options OFF on Apple, so we don't pass them there.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Make cvcpkg deps discoverable: wayland-scanner (PATH), X11/wayland .pc
# files (PKG_CONFIG_PATH) and FindX11 search roots (CMAKE_PREFIX_PATH set
# by the shared env).
export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

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
    linux|freebsd)
        _glfw_opts+=( -DGLFW_BUILD_X11=ON -DGLFW_BUILD_WAYLAND=ON )
        ;;
    openbsd|netbsd)
        # Wayland is not a first-class platform on OpenBSD/NetBSD; X11 only.
        _glfw_opts+=( -DGLFW_BUILD_X11=ON -DGLFW_BUILD_WAYLAND=OFF )
        ;;
    macos)
        # Cocoa backend; GLFW forces X11/Wayland OFF on Apple.
        ;;
esac

cvc_cmake_build "${_glfw_opts[@]}"
