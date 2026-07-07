#!/usr/bin/env bash
# recipes/yaml/build-cosmo.sh — cross-compile libyaml with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DYAML_BUILD_TESTING=OFF \
    -DINSTALL_CMAKE_DIR="${CVC_INSTALL_DIR}/lib/cmake/yaml"
