#!/usr/bin/env bash
# recipes/yaml-cpp/build-wasm.sh — cross-compile yaml-cpp to wasm via Emscripten.
#
# yaml-cpp is a self-contained C++ parser/emitter: std::string, std::istream and
# the containers, nothing else.  No threads, no sockets, no dlopen, no platform
# #ifdefs — the library never even opens a file itself (callers hand it a stream).
#
# The one flag that must differ from the native build.sh is YAML_BUILD_SHARED_LIBS.
# yaml-cpp keys that option off BUILD_SHARED_LIBS but the native script forces it
# ON; wasm is static-only, so it is forced OFF here instead of inherited, which
# keeps the intent explicit rather than depending on the helper's default.
#
# YAML_CPP_BUILD_TESTS=OFF also matters for hermeticity, not just build time:
# yaml-cpp's test target pulls GoogleTest, which would mean a network fetch from
# inside the build.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DYAML_CPP_BUILD_TESTS=OFF \
    -DYAML_CPP_BUILD_TOOLS=OFF \
    -DYAML_CPP_BUILD_CONTRIB=OFF \
    -DYAML_BUILD_SHARED_LIBS=OFF
