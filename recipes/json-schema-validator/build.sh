#!/usr/bin/env bash
# recipes/json-schema-validator/build.sh — build pboettch/json-schema-validator.
# A small CMake C++ library over nlohmann/json. It does find_package(nlohmann_json)
# when nlohmann_json is not already a target, so nlohmann-json must be in the build
# prefix (declared as a build dep) — otherwise its CMake FetchContent-downloads it,
# which a hermetic build must not do. JSON_VALIDATOR_SHARED_LIBS tracks the recipe
# link mode (build.sh sets BUILD_SHARED_LIBS ON for shared, OFF for static).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DJSON_VALIDATOR_BUILD_TESTS=OFF \
    -DJSON_VALIDATOR_BUILD_EXAMPLES=OFF \
    -DJSON_VALIDATOR_INSTALL=ON \
    -DJSON_VALIDATOR_SHARED_LIBS="${BUILD_SHARED_LIBS}"
