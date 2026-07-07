#!/usr/bin/env bash
# recipes/x265/build.sh — build x265 H.265/HEVC encoder via CMake.
#
# The CMakeLists.txt lives in the "source/" subdirectory of the x265
# tarball.  Assembly optimisations are enabled automatically when nasm
# is on PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

_shared=ON
_static=OFF
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _shared=OFF
    _static=ON
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/source" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DBUILD_SHARED_LIBS="${_shared}" \
    -DENABLE_SHARED="${_shared}" \
    -DENABLE_STATIC="${_static}" \
    -DENABLE_CLI=OFF \
    -DENABLE_TESTS=OFF \
    -DLIB_INSTALL_DIR=lib

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

cvc_rewrite_install_paths
