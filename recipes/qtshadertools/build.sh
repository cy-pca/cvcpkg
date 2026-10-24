#!/usr/bin/env bash
# recipes/qtshadertools/build.sh — build Qt ShaderTools module on Linux and macOS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# OpenBSD: no -Wl,--no-undefined (see recipes/qt6/build.sh): libc is linked into
# executables there, not shared libraries.
_openbsd_flags=()
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    _openbsd_flags+=(-DHAVE_DASH_UNDEFINED_SYMBOLS=OFF -DHAVE_DASH_DASH_NO_UNDEFINED=OFF)
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF \
    ${_openbsd_flags[@]+"${_openbsd_flags[@]}"}
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
