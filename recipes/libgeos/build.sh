#!/usr/bin/env bash
# recipes/libgeos/build.sh — GEOS (libgeos + libgeos_c) via CMake (root CMakeLists).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Library only — skip the unit-test suite and micro-benchmarks. GEOS requires
# C++14 minimum; cvc_cmake_build's C++17 default satisfies it.
cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_BENCHMARKS=OFF
