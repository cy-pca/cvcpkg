#!/usr/bin/env bash
# recipes/yaml-cpp/build-wasi.sh — cross-compile yaml-cpp to wasm32-wasi.
#
# Parsing YAML is pure computation over a std::istream, so none of the wasip1
# gaps apply: yaml-cpp starts no threads, opens no sockets, never calls dlopen
# or fork, and leaves file handling to the caller.
#
# It does throw (YAML::ParserException and friends), which is the one thing
# worth naming for a wasip1 target — C++ exceptions work here, as libgeos and
# libspatialindex, both exception-driven C++, already demonstrate at this target.
#
# YAML_BUILD_SHARED_LIBS=OFF: the native build.sh forces it ON, and wasi is
# static-only, so it must be overridden rather than inherited.
# YAML_CPP_BUILD_TESTS=OFF additionally keeps the build hermetic — the test
# target fetches GoogleTest.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DYAML_CPP_BUILD_TESTS=OFF \
    -DYAML_CPP_BUILD_TOOLS=OFF \
    -DYAML_CPP_BUILD_CONTRIB=OFF \
    -DYAML_BUILD_SHARED_LIBS=OFF
