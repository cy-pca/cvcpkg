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

mkdir -p "${SSL_DIR}/certs" "${SHARE_DIR}"

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

# One symlink per certificate in the bundle, populating etc/ssl/certs/.
# The targets are RELATIVE on purpose: cvcpkg's extraction filter rejects
# absolute link targets (tarfile.AbsoluteLinkError) and aborts the pack —
# see the same fix in recipes/bzip2/build.sh.
#
# NOTE: these are numbered (1.pem, 2.pem, ...), not subject-hash named, so
# OpenSSL's SSL_CERT_DIR hashed-directory lookup does not actually consult
# them; SSL_CERT_FILE (the bundle itself) is what makes verification work.
# Naming them properly needs c_rehash/`openssl x509 -hash`, i.e. an openssl
# build-time dependency this recipe does not declare.  Behaviour preserved
# from the original script; see the follow-up issue.
CERT_COUNT="$(grep -c '^-----BEGIN CERTIFICATE-----' "${SSL_DIR}/cert.pem")"
(
    cd "${SSL_DIR}/certs"
    i=1
    while [ "${i}" -le "${CERT_COUNT}" ]; do
        ln -sf ../cert.pem "${i}.pem"
        i=$((i + 1))
    done
)

# Convenience env hook.  Quoted heredoc: nothing is expanded at build time.
# The hook derives the prefix from its OWN path (<prefix>/share/ca-bundle/
# env.sh) rather than baking in CVC_INSTALL_DIR, because the package is
# unpacked at whatever prefix the consumer chooses — and _common/
# rewrite-install-paths.sh only rewrites .pc/.cmake files, never .sh.
cat > "${SHARE_DIR}/env.sh" << 'EOF'
# ca-bundle environment hook -- source this to point OpenSSL at the bundled certs
_ca_bundle_self="${BASH_SOURCE[0]:-$0}"
_ca_bundle_prefix="$(cd "$(dirname "${_ca_bundle_self}")/../.." && pwd)"
export SSL_CERT_FILE="${_ca_bundle_prefix}/etc/ssl/cert.pem"
export SSL_CERT_DIR="${_ca_bundle_prefix}/etc/ssl/certs"
unset _ca_bundle_self _ca_bundle_prefix
EOF

echo "ca-bundle: staged ${CERT_COUNT} certificates to ${SSL_DIR}"
