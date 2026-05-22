#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Temporarily remove protobuf-installed upb headers from the prefix.
# grpc builds its own upb from third_party/upb, and the protobuf-installed
# upb headers at prefix/include/upb/ are a different version, causing
# compile-time symbol mismatches.  We keep the libupb* libraries in
# place so protobuf's cmake config validates successfully.
_upb_backup=""
if [[ -d "${CVC_DEPS_PREFIX}/include/upb" ]]; then
    _upb_backup="$(mktemp -d)"
    mv "${CVC_DEPS_PREFIX}/include/upb" "${_upb_backup}/upb"
    if [[ -d "${CVC_DEPS_PREFIX}/include/upb_generator" ]]; then
        mv "${CVC_DEPS_PREFIX}/include/upb_generator" "${_upb_backup}/upb_generator"
    fi
    echo "cvcpkg: moved prefix/include/upb{,_generator} aside to avoid header collision"
fi

cvc_cmake_build \
    -DgRPC_BUILD_TESTS=OFF \
    -DgRPC_BUILD_CSHARP_EXT=OFF \
    -DgRPC_BUILD_GRPC_CPP_PLUGIN=ON \
    -DgRPC_BUILD_GRPC_CSHARP_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_NODE_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_OBJECTIVE_C_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_PHP_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_PYTHON_PLUGIN=ON \
    -DgRPC_BUILD_GRPC_RUBY_PLUGIN=OFF \
    -DgRPC_ABSL_PROVIDER=package \
    -DgRPC_CARES_PROVIDER=package \
    -DgRPC_PROTOBUF_PROVIDER=package \
    -DgRPC_RE2_PROVIDER=package \
    -DgRPC_SSL_PROVIDER=package \
    -DgRPC_ZLIB_PROVIDER=package \
    -DCMAKE_CXX_STANDARD=17

# Restore the upb headers so downstream consumers can use them.
if [[ -n "${_upb_backup}" ]]; then
    if [[ -d "${_upb_backup}/upb" ]]; then
        mv "${_upb_backup}/upb" "${CVC_DEPS_PREFIX}/include/upb"
    fi
    if [[ -d "${_upb_backup}/upb_generator" ]]; then
        mv "${_upb_backup}/upb_generator" "${CVC_DEPS_PREFIX}/include/upb_generator"
    fi
    rm -rf "${_upb_backup}"
    echo "cvcpkg: restored prefix/include/upb headers"
fi
