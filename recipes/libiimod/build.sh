#!/usr/bin/env bash
# recipes/libiimod/build.sh — build libiimod from vendored sources.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# CVC_SOURCE_DIR is set by the build system to the resolved
# vendored source tree (works for both local and remote builds).
LIBIIMOD_SRC="${CVC_SOURCE_DIR}"

cmake -G Ninja \
    -S "${LIBIIMOD_SRC}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
