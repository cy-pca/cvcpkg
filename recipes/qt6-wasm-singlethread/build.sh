#!/usr/bin/env bash
# recipes/qt6-wasm-singlethread/build.sh — cross-compile Qt 6 Base
# for wasm_singlethread using Emscripten.
#
# This script:
#   1. Builds a minimal native "host Qt" (moc, rcc, uic) from the
#      same source tree.
#   2. Activates the Emscripten SDK from the emsdk bundle.
#   3. Configures Qt for the wasm-emscripten target with threads off.
#   4. Installs the WASM libraries + the native host tools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_HOST_PLATFORM:-${CVC_PLATFORM}}.sh"

: "${CVC_EMSDK_DIR:?CVC_EMSDK_DIR must point to the activated emsdk bundle}"

# --- Step 1: Build a native host Qt (needed for moc/rcc/uic). ---
HOST_BUILD_DIR="${CVC_BUILD_DIR}/host-qt"
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${HOST_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_BUILD_DIR}/host-qt-install" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF
cmake --build "${HOST_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${HOST_BUILD_DIR}"

# --- Step 2: Activate Emscripten. ---
source "${CVC_EMSDK_DIR}/emsdk_env.sh"

# --- Step 3: Configure Qt for wasm_singlethread. ---
WASM_BUILD_DIR="${CVC_BUILD_DIR}/wasm-qt"
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${WASM_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DQT_HOST_PATH="${CVC_BUILD_DIR}/host-qt-install" \
    -DFEATURE_thread=OFF \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF \
    -DINPUT_opengl=es2 \
    -DFEATURE_sql_mysql=OFF \
    -DFEATURE_sql_psql=OFF

# --- Step 4: Build and install. ---
cmake --build "${WASM_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${WASM_BUILD_DIR}"

# Also copy the native host tools into the install prefix for convenience.
for tool in moc rcc uic qmake6; do
    if [[ -f "${CVC_BUILD_DIR}/host-qt-install/bin/${tool}" ]]; then
        cp "${CVC_BUILD_DIR}/host-qt-install/bin/${tool}" "${CVC_INSTALL_DIR}/bin/"
    fi
done

echo "Qt 6 wasm_singlethread installed to ${CVC_INSTALL_DIR}"
