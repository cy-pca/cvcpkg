#!/usr/bin/env bash
# recipes/slint/build-wasm.sh — Slint C++ bindings for wasm32-emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_HOST_PLATFORM:-${CVC_PLATFORM}}.sh"

: "${CVC_EMSDK_DIR:?CVC_EMSDK_DIR must point to the activated emsdk bundle}"

SLINT_VERSION="1.13.0"
TARBALL="v${SLINT_VERSION}.tar.gz"
URL="https://github.com/slint-ui/slint/archive/refs/tags/${TARBALL}"

SRC="${CVC_SOURCE_DIR}"
if [[ ! -f "${SRC}/CMakeLists.txt" ]]; then
    curl -fSL -o "${CVC_BUILD_DIR}/${TARBALL}" "${URL}"
    mkdir -p "${SRC}"
    tar xf "${CVC_BUILD_DIR}/${TARBALL}" -C "${SRC}" --strip-components=1
fi

# Rust for wasm target.
if ! command -v cargo >/dev/null 2>&1; then
    export CARGO_HOME="${CVC_BUILD_DIR}/cargo"
    export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain stable
    export PATH="${CARGO_HOME}/bin:${PATH}"
fi
rustup target add wasm32-unknown-emscripten || true

source "${CVC_EMSDK_DIR}/emsdk_env.sh"

cmake -G Ninja \
    -S "${SRC}" \
    -B "${CVC_BUILD_DIR}/wasm" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DSLINT_BUILD_EXAMPLES=OFF \
    -DSLINT_BUILD_TESTING=OFF \
    -DSLINT_FEATURE_COMPILER=OFF \
    -DRust_CARGO_TARGET=wasm32-unknown-emscripten
cmake --build "${CVC_BUILD_DIR}/wasm" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/wasm"

echo "slint ${SLINT_VERSION} (wasm) installed to ${CVC_INSTALL_DIR}"
