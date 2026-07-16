#!/usr/bin/env bash
# recipes/glew/build.sh — build GLEW from source with CMake.
#
# GLEW's CMake project lives in build/cmake (not the repo root), so point
# CVC_SOURCE_DIR there before invoking the shared helper. Links the system
# OpenGL (GL is a driver-provided system library) — on Linux this expects
# Mesa/GL dev headers on the build host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake"

cvc_cmake_build \
    -DBUILD_UTILS=OFF \
    -DGLEW_REGAL=OFF \
    -DGLEW_OSMESA=OFF
