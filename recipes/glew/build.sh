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

# On Linux, GL and Xlib come from cvcpkg's libglvnd and libx11 (recipe.yaml),
# so FindOpenGL/FindX11 must search the deps prefix.
_glew_extra=()
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    _glew_extra+=(-DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}")
fi

cvc_cmake_build \
    -DBUILD_UTILS=OFF \
    -DGLEW_REGAL=OFF \
    -DGLEW_OSMESA=OFF \
    ${_glew_extra[@]+"${_glew_extra[@]}"}
