#!/usr/bin/env bash
# recipes/wasmedge/build.sh — build WasmEdge from source on Linux/macOS.
# Requires LLVM >= 14 dev libraries installed on the host.
# See recipe.yaml toolchain section for install instructions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Point CMake at the system LLVM if a versioned install exists.
if [[ -d /usr/lib/llvm-18 ]]; then
    export LLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm
    export LLD_DIR=/usr/lib/llvm-18/lib/cmake/lld
elif command -v llvm-config &>/dev/null; then
    export LLVM_DIR="$(llvm-config --cmakedir)"
fi

cvc_cmake_build \
    -DWASMEDGE_USE_LLVM=ON \
    -DWASMEDGE_BUILD_TESTS=OFF \
    -DWASMEDGE_BUILD_TOOLS=OFF \
    -DWASMEDGE_BUILD_PLUGINS=ON \
    -DWASMEDGE_BUILD_SHARED_LIB=ON \
    -DWASMEDGE_BUILD_STATIC_LIB=OFF
