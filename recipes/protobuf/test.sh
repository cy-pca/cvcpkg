#!/usr/bin/env bash
# recipes/protobuf/test.sh — smoke test for protobuf install.
# A native bundle must run protoc; a wasm one ships libprotobuf only (no protoc), so there the
# libraries and CMake packages are checked instead.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- protobuf smoke test (${CVC_PLATFORM:-native}) --"

test -f "${CVC_INSTALL_DIR}/include/google/protobuf/message.h" \
    || { echo "FAIL: protobuf headers not found"; exit 1; }

if [[ "${CVC_PLATFORM:-}" == wasm* ]]; then
    test -f "${CVC_INSTALL_DIR}/lib/libprotobuf.a" \
        || { echo "FAIL: libprotobuf.a not found"; exit 1; }
    test -f "${CVC_INSTALL_DIR}/lib/cmake/protobuf/protobuf-config.cmake" \
        || { echo "FAIL: the protobuf CMake package not found"; exit 1; }
    test -f "${CVC_INSTALL_DIR}/lib/cmake/utf8_range/utf8_range-config.cmake" \
        || { echo "FAIL: the utf8_range CMake package (protobuf needs it) not found"; exit 1; }
    echo "  OK: libprotobuf and its CMake packages"
else
    test -x "${CVC_INSTALL_DIR}/bin/protoc" \
        || { echo "FAIL: protoc not found"; exit 1; }
    "${CVC_INSTALL_DIR}/bin/protoc" --version
    echo "  OK: protoc runs"
fi

echo "-- protobuf smoke test passed --"
