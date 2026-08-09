#!/usr/bin/env bash
# recipes/yaml/build-wasm.sh — cross-compile libyaml to wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DYAML_BUILD_TESTING=OFF \
    -DINSTALL_CMAKE_DIR="${CVC_INSTALL_DIR}/lib/cmake/yaml"
