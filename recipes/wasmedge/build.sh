#!/usr/bin/env bash
# recipes/wasmedge/build.sh — build WasmEdge from source on Linux/macOS.
# Requires LLVM >= 14 dev libraries installed on the host.
# See recipe.yaml toolchain section for install instructions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Point CMake at the system LLVM.
# 1. Linux: check /usr/lib/llvm-{ver} in priority order.
# 2. macOS: check Homebrew llvm@{ver} prefixes.
# 3. Fallback: llvm-config on PATH.
LLVM_FOUND=false

# --- Linux versioned installs ---
for ver in 18 20 19 17 16 15 14; do
    llvm_prefix="/usr/lib/llvm-${ver}"
    if [[ -d "${llvm_prefix}/lib/cmake/llvm" ]]; then
        export LLVM_DIR="${llvm_prefix}/lib/cmake/llvm"
        [[ -d "${llvm_prefix}/lib/cmake/lld" ]] && export LLD_DIR="${llvm_prefix}/lib/cmake/lld"
        LLVM_FOUND=true
        echo "Using LLVM ${ver} at ${llvm_prefix}"
        break
    fi
done

# --- macOS Homebrew installs ---
if [[ "$LLVM_FOUND" = false ]] && command -v brew &>/dev/null; then
    for ver in 18 20 19 17 16 15 14; do
        brew_prefix="$(brew --prefix llvm@"${ver}" 2>/dev/null || true)"
        if [[ -n "$brew_prefix" && -d "${brew_prefix}/lib/cmake/llvm" ]]; then
            export LLVM_DIR="${brew_prefix}/lib/cmake/llvm"
            [[ -d "${brew_prefix}/lib/cmake/lld" ]] && export LLD_DIR="${brew_prefix}/lib/cmake/lld"
            LLVM_FOUND=true
            echo "Using Homebrew LLVM ${ver} at ${brew_prefix}"
            break
        fi
    done
fi

# --- Fallback: llvm-config on PATH ---
if [[ "$LLVM_FOUND" = false ]] && command -v llvm-config &>/dev/null; then
    export LLVM_DIR="$(llvm-config --cmakedir)"
    lld_candidate="$(dirname "$LLVM_DIR")/lld"
    [[ -d "$lld_candidate" ]] && export LLD_DIR="$lld_candidate"
    echo "Using LLVM from llvm-config: $LLVM_DIR"
fi

cvc_cmake_build \
    -DWASMEDGE_USE_LLVM=ON \
    -DWASMEDGE_BUILD_TESTS=OFF \
    -DWASMEDGE_BUILD_TOOLS=OFF \
    -DWASMEDGE_BUILD_PLUGINS=ON \
    -DWASMEDGE_BUILD_SHARED_LIB=ON \
    -DWASMEDGE_BUILD_STATIC_LIB=OFF
