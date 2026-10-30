#!/usr/bin/env bash
# recipes/pystring/build.sh — build pystring with the recipe's own CMakeLists.txt
# (see the comment at its top for why upstream's is replaced).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cp "${SCRIPT_DIR}/CMakeLists.txt" "${CVC_SOURCE_DIR}/CMakeLists.txt"

cvc_cmake_build
