#!/usr/bin/env bash
# recipes/ca-bundle/build.sh — stage the Mozilla CA bundle into the prefix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

CERT_URL="https://curl.se/ca/cacert-2026-05-14.pem"
CERT_SHA256="86a1f3366afac7c6f8ae9f3c779ac221129328c43f0ab2b8817eb2f362a5025c"

SSL_DIR="${CVC_INSTALL_DIR}/etc/ssl"
SHARE_DIR="${CVC_INSTALL_DIR}/share/ca-bundle"

mkdir -p "${SSL_DIR}" "${SHARE_DIR}"

# Download and verify the CA bundle.
curl -fsSL --retry 5 --retry-delay 3 -o "${SSL_DIR}/cert.pem" "${CERT_URL}"

# sha256 tooling differs across this recipe's matrix: coreutils sha256sum on
# Linux, sha256(1) on the BSDs, shasum/openssl on macOS.  Same fallback chain
# as recipes/grpc/build.sh.
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(sha256sum "${SSL_DIR}/cert.pem" | awk '{print $1}')"
elif command -v sha256 >/dev/null 2>&1; then
    ACTUAL_SHA256="$(sha256 -q "${SSL_DIR}/cert.pem")"
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(shasum -a 256 "${SSL_DIR}/cert.pem" | awk '{print $1}')"
else
    ACTUAL_SHA256="$(openssl dgst -sha256 "${SSL_DIR}/cert.pem" | awk '{print $NF}')"
fi
if [ "${ACTUAL_SHA256}" != "${CERT_SHA256}" ]; then
    echo "SHA256 mismatch: expected ${CERT_SHA256}, got ${ACTUAL_SHA256}" >&2
    exit 1
fi

CERT_COUNT="$(grep -c '^-----BEGIN CERTIFICATE-----' "${SSL_DIR}/cert.pem")"

# No etc/ssl/certs/ directory is produced, on purpose -- see the "Usage"
# note in recipe.yaml.  SSL_CERT_DIR resolves certificates only through
# OpenSSL's hashed-directory lookup (<subject-hash>.<seq>), which requires
# c_rehash and therefore an openssl binary at build time; SSL_CERT_FILE
# covers the use case with the single bundle.

# Convenience env hook.  Quoted heredoc: nothing is expanded at build time.
# The hook derives the prefix from its OWN path (<prefix>/share/ca-bundle/
# env.sh) rather than baking in CVC_INSTALL_DIR, because the package is
# unpacked at whatever prefix the consumer chooses — and _common/
# rewrite-install-paths.sh only rewrites .pc/.cmake files, never .sh.
cat > "${SHARE_DIR}/env.sh" << 'EOF'
# ca-bundle environment hook -- source this to point OpenSSL at the bundled certs
# SSL_CERT_DIR is deliberately not set: this package ships only the single-file
# bundle, and an unhashed directory would never be read by OpenSSL anyway.
_ca_bundle_self="${BASH_SOURCE[0]:-$0}"
_ca_bundle_prefix="$(cd "$(dirname "${_ca_bundle_self}")/../.." && pwd)"
export SSL_CERT_FILE="${_ca_bundle_prefix}/etc/ssl/cert.pem"
unset _ca_bundle_self _ca_bundle_prefix
EOF

echo "ca-bundle: staged ${CERT_COUNT} certificates to ${SSL_DIR}"
