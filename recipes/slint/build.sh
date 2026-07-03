#!/usr/bin/env bash
# recipes/slint/build.sh — build Slint C++ bindings on Linux/macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

SLINT_VERSION="1.13.0"
TARBALL="v${SLINT_VERSION}.tar.gz"
URL="https://github.com/slint-ui/slint/archive/refs/tags/${TARBALL}"

# --- 1. Fetch source. ---
SRC="${CVC_SOURCE_DIR}"
if [[ ! -f "${SRC}/CMakeLists.txt" ]]; then
    echo "Downloading ${URL} ..."
    curl -fSL -o "${CVC_BUILD_DIR}/${TARBALL}" "${URL}"
    mkdir -p "${SRC}"
    tar xf "${CVC_BUILD_DIR}/${TARBALL}" -C "${SRC}" --strip-components=1
fi

# --- 2. Ensure rustc/cargo are available. ---
if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo not found on PATH; installing rustup toolchain into build tree ..."
    export CARGO_HOME="${CVC_BUILD_DIR}/cargo"
    export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain stable
    export PATH="${CARGO_HOME}/bin:${PATH}"
fi

# --- 3. CMake build. ---
cmake -G Ninja \
    -S "${SRC}" \
    -B "${CVC_BUILD_DIR}/cmake" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DSLINT_BUILD_EXAMPLES=OFF \
    -DSLINT_BUILD_TESTING=OFF \
    -DSLINT_FEATURE_COMPILER=ON
cmake --build "${CVC_BUILD_DIR}/cmake" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/cmake"

echo "slint ${SLINT_VERSION} installed to ${CVC_INSTALL_DIR}"
