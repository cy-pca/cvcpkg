#!/usr/bin/env bash
# recipes/qtmultimedia/build-wasm.sh — cross-compile Qt Multimedia for
# wasm_singlethread using Emscripten.
#
# WebAssembly builds use Qt's dedicated WASM media backend (playback via
# HTMLMediaElement) — the FFmpeg / GStreamer / PipeWire / PulseAudio
# runtime backends we ship on Linux and macOS are not applicable and
# aren't linked here.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

_host_qt="${CVC_DEPS_PREFIX}/host-qt"
if [[ ! -x "${_host_qt}/bin/qmake6" && ! -x "${_host_qt}/libexec/qmake6" ]]; then
    echo "error: expected host Qt at ${_host_qt} (from qt6 wasm bundle)" >&2
    echo "       is the installed qt6 bundle at least cvc.3?" >&2
    exit 1
fi

cd "${CVC_SOURCE_DIR}"

# Disable the desktop backends explicitly: on wasm none of them are
# reachable and leaving them auto-detect can trip Qt's plugin discovery
# on the host Qt install we're pointing QT_HOST_PATH at.
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_FIND_ROOT_PATH="${CVC_DEPS_PREFIX}" \
    -DQT_HOST_PATH="${_host_qt}" \
    -DFEATURE_ffmpeg=OFF \
    -DFEATURE_gstreamer=OFF \
    -DFEATURE_pulseaudio=OFF \
    -DFEATURE_pipewire=OFF \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

cvc_rewrite_install_paths
