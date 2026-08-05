#!/usr/bin/env bash
# recipes/yaml-cpp/build-cosmo.sh — cross-compile yaml-cpp with Cosmopolitan.
#
# Nothing in yaml-cpp is platform-conditional — it is standard C++ over the
# containers and iostreams — and cosmocc ships a C++ toolchain that already
# carries several C++ libraries in this catalog (abseil, re2, libgeos,
# libspatialindex).  Static-only and no-dlopen cost nothing here.
#
# YAML_BUILD_SHARED_LIBS=OFF because the native build.sh forces it ON and an APE
# archive is necessarily static.  YAML_CPP_BUILD_TESTS=OFF keeps the build
# hermetic; that target fetches GoogleTest.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DYAML_CPP_BUILD_TESTS=OFF \
    -DYAML_CPP_BUILD_TOOLS=OFF \
    -DYAML_CPP_BUILD_CONTRIB=OFF \
    -DYAML_BUILD_SHARED_LIBS=OFF
