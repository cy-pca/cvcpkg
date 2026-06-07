#!/usr/bin/env bash
# recipes/pcre2/test.sh — smoke-test PCRE2 installation.
set -euo pipefail

# Skip runtime tests for cross-compiled (wasm/wasi) builds.
if [[ "${CVC_PLATFORM:-}" == wasm || "${CVC_PLATFORM:-}" == wasi ]]; then
    echo 'pcre2 test skipped (cross-compiled target).'
    exit 0
fi

echo 'Testing pcre2-config...'
"${CVC_INSTALL_DIR}/bin/pcre2-config" --version

echo 'Testing pcre2grep...'
echo "hello world" | "${CVC_INSTALL_DIR}/bin/pcre2grep" "hello"

echo 'Testing pkg-config...'
PKG_CONFIG_PATH="${CVC_INSTALL_DIR}/lib/pkgconfig" pkg-config --modversion libpcre2-8

echo 'pcre2 test passed.'
