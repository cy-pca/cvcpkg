#!/usr/bin/env bash
# recipes/openssl/build.sh — build OpenSSL from source.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

case "${CVC_PLATFORM}" in
    macos)
        TARGET="darwin64-arm64-cc"
        [[ "$(uname -m)" == "x86_64" ]] && TARGET="darwin64-x86_64-cc"
        ;;
    freebsd|openbsd|netbsd)
        TARGET="BSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="BSD-aarch64"
        ;;
    *)
        TARGET="linux-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="linux-aarch64"
        ;;
esac

./Configure "${TARGET}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --openssldir="${CVC_INSTALL_DIR}/etc/ssl" \
    shared \
    no-tests

make -j "${CVC_JOBS}"
make install_sw install_ssldirs
