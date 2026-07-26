#!/usr/bin/env bash
# recipes/libsamplerate/build.sh — build libsamplerate (Secret Rabbit Code)
# with CMake.
#
# With examples and tests off, libsamplerate has no external dependencies
# (libsndfile is only used by the examples/tests).  The shared cvc_cmake_build
# helper sets BUILD_SHARED_LIBS from CVC_LINK, and the project's plain
# add_library(samplerate ...) honors it — static and shared are both produced
# correctly, so no project-specific link toggle is needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DLIBSAMPLERATE_EXAMPLES=OFF \
    -DLIBSAMPLERATE_INSTALL=ON
