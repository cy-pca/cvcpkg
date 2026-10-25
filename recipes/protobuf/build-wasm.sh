#!/usr/bin/env bash
# recipes/protobuf/build-wasm.sh — cross-compile libprotobuf to wasm.
#
# Only the runtime libraries: a wasm build cannot run protoc, so a consumer generates its .pb.cc
# with a native protoc of the same version (28.3) and links this libprotobuf. utf8_range, which
# protobuf builds and its CMake config requires, ships with it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -Dprotobuf_BUILD_TESTS=OFF \
    -Dprotobuf_BUILD_EXAMPLES=OFF \
    -Dprotobuf_BUILD_CONFORMANCE=OFF \
    -Dprotobuf_BUILD_PROTOC_BINARIES=OFF \
    -Dprotobuf_BUILD_LIBPROTOC=OFF \
    -Dprotobuf_BUILD_LIBUPB=OFF \
    -Dprotobuf_ABSL_PROVIDER=package \
    -Dprotobuf_WITH_ZLIB=ON \
    -DCMAKE_CXX_STANDARD=17
