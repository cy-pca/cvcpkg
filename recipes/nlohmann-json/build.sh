#!/usr/bin/env bash
# recipes/nlohmann-json/build.sh — install nlohmann/json (header-only) from source.
# Plain CMake INTERFACE library: JSON_Install=ON installs the headers, the CMake
# package config (nlohmann_json::nlohmann_json), and a pkg-config file. No tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DJSON_BuildTests=OFF \
    -DJSON_Install=ON \
    -DJSON_MultipleHeaders=ON
