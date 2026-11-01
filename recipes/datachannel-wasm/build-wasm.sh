#!/usr/bin/env bash
# recipes/datachannel-wasm/build-wasm.sh -- build datachannel-wasm with Emscripten (linux host).
#
# Upstream's CMakeLists builds the static library but installs nothing, so the
# build runs there and the install is done here: the library, the public
# headers (wasm/include/rtc), the two JS libraries and the package config
# beside this script.  -fexceptions: the rtc:: API reports errors by throwing,
# and libcvc's wasm build catches them (it compiles with -fexceptions too).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

export CXXFLAGS="-fexceptions ${CXXFLAGS:-}"

emcmake cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"

mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/datachannel-wasm" "${CVC_INSTALL_DIR}/include/rtc" \
    "${CVC_INSTALL_DIR}/share/datachannel-wasm"
cp "${CVC_BUILD_DIR}/libdatachannel-wasm.a" "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_SOURCE_DIR}"/wasm/include/rtc/*.hpp "${CVC_INSTALL_DIR}/include/rtc/"
cp "${CVC_SOURCE_DIR}"/wasm/js/webrtc.js "${CVC_SOURCE_DIR}"/wasm/js/websocket.js \
    "${CVC_INSTALL_DIR}/share/datachannel-wasm/"
cp "${CVC_SOURCE_DIR}/LICENSE" "${CVC_INSTALL_DIR}/share/datachannel-wasm/LICENSE"
cp "${SCRIPT_DIR}/datachannel-wasmConfig.cmake" "${CVC_INSTALL_DIR}/lib/cmake/datachannel-wasm/"
