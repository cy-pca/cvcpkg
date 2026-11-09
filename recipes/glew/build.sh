#!/usr/bin/env bash
# recipes/glew/build.sh — build GLEW from source with CMake.
#
# GLEW's CMake project lives in build/cmake (not the repo root), so point
# CVC_SOURCE_DIR there before invoking the shared helper. On Linux it links
# cvcpkg's libglvnd (the GL dispatch; drivers stay system-provided); elsewhere
# the system OpenGL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake"

# On Linux, GL, GLU and Xlib (with libSM/libICE) come from cvcpkg's libglvnd,
# glu, libx11, libsm and libice (recipe.yaml), so FindOpenGL/FindX11 must
# search the deps prefix.
#
# On macOS, FindOpenGL resolves OpenGL.framework inside the build host's Xcode
# SDK (/Applications/Xcode_<ver>.app/.../OpenGL.framework), and GLEW exports
# that absolute path in GLEW::glew's link interface, which exists on no other
# Mac. Name the framework instead.
_glew_extra=()
case "${CVC_PLATFORM}" in
    linux)
        _glew_extra+=(-DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}")
        ;;
    macos)
        _glew_extra+=(
            "-DOPENGL_gl_LIBRARY=-framework OpenGL"
            "-DOPENGL_glu_LIBRARY=-framework OpenGL"
        )
        ;;
esac

cvc_cmake_build \
    -DBUILD_UTILS=OFF \
    -DGLEW_REGAL=OFF \
    -DGLEW_OSMESA=OFF \
    ${_glew_extra[@]+"${_glew_extra[@]}"}
