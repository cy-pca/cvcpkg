#!/usr/bin/env bash
# recipes/sdl3/build.sh — build SDL 3.x from source with CMake.
#
# SDL dlopens most of its platform backends (X11, Wayland, PulseAudio,
# PipeWire, ALSA, ...) at runtime, so the dependency libraries only need
# to be discoverable at build time for their headers.  Expose the cvcpkg
# dependency prefix to pkg-config and CMake before configuring so SDL
# finds the X11 / Wayland / audio headers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"

# Honour CVC_LINK via SDL's own static/shared switch.  cvc_cmake_build
# also passes -DBUILD_SHARED_LIBS, but SDL_STATIC / SDL_SHARED take
# precedence and let us emit exactly one artifact per bundle.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _sdl_static=ON
    _sdl_shared=OFF
else
    _sdl_static=OFF
    _sdl_shared=ON
fi

cvc_cmake_build \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DSDL_STATIC="${_sdl_static}" \
    -DSDL_SHARED="${_sdl_shared}" \
    -DSDL_TEST_LIBRARY=OFF \
    -DSDL_TESTS=OFF \
    -DSDL_EXAMPLES=OFF \
    -DSDL_INSTALL_TESTS=OFF
