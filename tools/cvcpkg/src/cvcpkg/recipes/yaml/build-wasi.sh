#!/usr/bin/env bash
# recipes/yaml/build-wasi.sh — cross-compile libyaml to wasm32-wasi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DYAML_BUILD_TESTING=OFF \
    -DINSTALL_CMAKE_DIR="${CVC_INSTALL_DIR}/lib/cmake/yaml"
