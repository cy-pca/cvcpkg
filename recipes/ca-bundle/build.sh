#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/build-vars.sh"

CERT_URL="https://curl.se/ca/cacert-2026-05-14.pem"
CERT_SHA256="86a1f3366afac7c6f8ae9f3c779ac221129328c43f0ab2b8817eb2f362a5025c"

mkdir -p "${PREFIX}/etc/ssl/certs" "${PREFIX}/share/ca-bundle"

# Download and verify the CA bundle
curl -fsSL "${CERT_URL}" -o "${PREFIX}/etc/ssl/cert.pem"
ACTUAL_SHA256=$(sha256sum "${PREFIX}/etc/ssl/cert.pem" | awk '{print $1}')
if [ "${ACTUAL_SHA256}" != "${CERT_SHA256}" ]; then
    echo "SHA256 mismatch: expected ${CERT_SHA256}, got ${ACTUAL_SHA256}" >&2
    exit 1
fi

# Create individual cert symlinks for each CA entry
cd "${PREFIX}/etc/ssl"
# Split the PEM into individual certs and create symlinks
awk '
    /-----BEGIN CERTIFICATE-----/ { n++ }
    n && /-----END CERTIFICATE-----/ {
        fname = "certs/" n ".pem"
        system("ln -sf ../cert.pem " fname)
    }
' cert.pem

# Create convenience env hook
cat > "${PREFIX}/share/ca-bundle/env.sh" << EOF
# ca-bundle environment hook -- source this to point OpenSSL at the bundled certs
export SSL_CERT_FILE="${PREFIX}/etc/ssl/cert.pem"
export SSL_CERT_DIR="${PREFIX}/etc/ssl/certs"
EOF
