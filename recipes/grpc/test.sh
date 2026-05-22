#!/usr/bin/env bash
# recipes/grpc/test.sh — smoke test for gRPC install.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "── gRPC smoke test ──"

test -f "${CVC_INSTALL_DIR}/include/grpcpp/grpcpp.h" \
    || { echo "FAIL: grpcpp headers not found"; exit 1; }

for tool in grpc_cpp_plugin grpc_python_plugin; do
    if [[ -x "${CVC_INSTALL_DIR}/bin/${tool}" ]]; then
        echo "  ✓ ${tool} found"
    else
        echo "  ⚠ ${tool} not found (may be OK on some platforms)"
    fi
done

echo "── gRPC smoke test passed ──"
