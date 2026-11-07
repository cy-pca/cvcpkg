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

# OpenBSD: SDL's own iconv (SDL_iconv: UTF-8/16/32, UCS-2/4, ASCII, Latin-1),
# as SDL itself chooses on macOS and Windows.  OpenBSD's libc has no iconv, so
# SDL_SYSTEM_ICONV linked the -liconv on the link path, the build host's ports
# libiconv.so.7.1 from /usr/local/lib (env-openbsd.sh), and cvc.4's libSDL3
# NEEDed it undeclared.
_sdl_extra=()
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    _sdl_extra+=(-DSDL_SYSTEM_ICONV=OFF)
fi

cvc_cmake_build \
    ${_sdl_extra[@]+"${_sdl_extra[@]}"} \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DSDL_STATIC="${_sdl_static}" \
    -DSDL_SHARED="${_sdl_shared}" \
    -DSDL_TEST_LIBRARY=OFF \
    -DSDL_TESTS=OFF \
    -DSDL_EXAMPLES=OFF \
    -DSDL_INSTALL_TESTS=OFF
