#!/usr/bin/env bash
# recipes/lerc/build-wasm.sh — cross-compile LERC to wasm via Emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build
