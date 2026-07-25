#!/usr/bin/env bash
# recipes/protobuf/test.sh — smoke test for protobuf install.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- protobuf smoke test --"

test -f "${CVC_INSTALL_DIR}/include/google/protobuf/message.h" \
    || { echo "FAIL: protobuf headers not found"; exit 1; }

test -x "${CVC_INSTALL_DIR}/bin/protoc" \
    || { echo "FAIL: protoc not found"; exit 1; }

"${CVC_INSTALL_DIR}/bin/protoc" --version
echo "  OK: protoc runs"

echo "-- protobuf smoke test passed --"
