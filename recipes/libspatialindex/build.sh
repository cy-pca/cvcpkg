#!/usr/bin/env bash
# recipes/libspatialindex/build.sh — libspatialindex via CMake (root CMakeLists).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Library only — the integrated gtest suite is off by default (BUILD_TESTING),
# set explicitly so the bundled test tree is never compiled.
cvc_cmake_build \
    -DBUILD_TESTING=OFF
