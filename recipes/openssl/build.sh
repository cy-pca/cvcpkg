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
    freebsd)
        TARGET="BSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="BSD-aarch64"
        ;;
    openbsd)
        # OpenBSD needs its own target — the generic BSD-* target
        # produces shared objects that don't link libc, which fails
        # with OpenBSD's default --no-undefined linker behaviour.
        TARGET="OpenBSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="OpenBSD-aarch64"
        ;;
    netbsd)
        TARGET="BSD-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="BSD-aarch64"
        ;;
    *)
        TARGET="linux-x86_64"
        [[ "$(uname -m)" == "aarch64" ]] && TARGET="linux-aarch64"
        ;;
esac

OPENSSL_OPTS=()
if [[ "${CVC_LINK}" == "static" ]]; then
    OPENSSL_OPTS+=(no-shared)
else
    OPENSSL_OPTS+=(shared)
fi

./Configure "${TARGET}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --libdir=lib \
    --openssldir="${CVC_INSTALL_DIR}/etc/ssl" \
    "${OPENSSL_OPTS[@]}" \
    no-tests

make -j "${CVC_JOBS}"
make install_sw install_ssldirs

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
