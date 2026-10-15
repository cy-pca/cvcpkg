#!/usr/bin/env bash
# recipes/secp256k1/build.sh — build libsecp256k1 from source (Linux, macOS, BSDs).
#
# Modules: ecdh, extrakeys and schnorrsig are on by default upstream; RECOVERY is
# off by default and coincurve calls secp256k1_ecdsa_sign_recoverable /
# secp256k1_ecdsa_recover, so it is switched on explicitly. All four are named
# here so a future upstream default flip cannot silently drop one.
# Tests, exhaustive tests, ctime tests and benchmarks are build-time only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DSECP256K1_ENABLE_MODULE_ECDH=ON \
    -DSECP256K1_ENABLE_MODULE_RECOVERY=ON \
    -DSECP256K1_ENABLE_MODULE_EXTRAKEYS=ON \
    -DSECP256K1_ENABLE_MODULE_SCHNORRSIG=ON \
    -DSECP256K1_BUILD_TESTS=OFF \
    -DSECP256K1_BUILD_EXHAUSTIVE_TESTS=OFF \
    -DSECP256K1_BUILD_CTIME_TESTS=OFF \
    -DSECP256K1_BUILD_BENCHMARK=OFF \
    -DSECP256K1_BUILD_EXAMPLES=OFF \
    -DCMAKE_INSTALL_LIBDIR=lib
