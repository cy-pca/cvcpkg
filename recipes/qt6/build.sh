#!/usr/bin/env bash
# recipes/qt6/build.sh — build Qt 6 Base from source on Linux and macOS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Qt6's CMake rejects build paths containing symlinks (macOS /var -> /private/var).
# Resolve the build dir to its real path.
CVC_BUILD_DIR="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "${CVC_BUILD_DIR}")"
export CVC_BUILD_DIR

cd "${CVC_SOURCE_DIR}"

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DINPUT_opengl=yes \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
