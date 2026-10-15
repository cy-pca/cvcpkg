#!/usr/bin/env bash
# recipes/secp256k1/test.sh — smoke test: link the staged library and run a real
# ECDSA sign/verify plus a recoverable signature round-trip (the recovery module
# is the one non-default module this recipe enables).
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- secp256k1 smoke test (${CVC_PLATFORM:-native}) --"
for h in secp256k1.h secp256k1_ecdh.h secp256k1_recovery.h secp256k1_extrakeys.h secp256k1_schnorrsig.h; do
    test -f "${CVC_INSTALL_DIR}/include/${h}" || { echo "FAIL: include/${h} missing"; exit 1; }
done

if ! command -v cc >/dev/null 2>&1; then
    echo "  WARN: no C compiler on PATH, skipping compile test"
    exit 0
fi

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/t.c" <<'EOF'
#include <stdio.h>
#include <string.h>
#include <secp256k1.h>
#include <secp256k1_recovery.h>
int main(void) {
    unsigned char seckey[32], msg[32];
    memset(seckey, 0x11, 32);
    memset(msg, 0x22, 32);
    secp256k1_context *ctx = secp256k1_context_create(SECP256K1_CONTEXT_NONE);
    secp256k1_pubkey pub, rpub;
    secp256k1_ecdsa_signature sig;
    secp256k1_ecdsa_recoverable_signature rsig;
    if (!secp256k1_ec_seckey_verify(ctx, seckey)) return 1;
    if (!secp256k1_ec_pubkey_create(ctx, &pub, seckey)) return 2;
    if (!secp256k1_ecdsa_sign(ctx, &sig, msg, seckey, NULL, NULL)) return 3;
    if (!secp256k1_ecdsa_verify(ctx, &sig, msg, &pub)) return 4;
    if (!secp256k1_ecdsa_sign_recoverable(ctx, &rsig, msg, seckey, NULL, NULL)) return 5;
    if (!secp256k1_ecdsa_recover(ctx, &rpub, &rsig, msg)) return 6;
    if (secp256k1_ec_pubkey_cmp(ctx, &pub, &rpub) != 0) return 7;
    secp256k1_context_destroy(ctx);
    puts("secp256k1 sign/verify/recover OK");
    return 0;
}
EOF
cc -o "${T}/t" "${T}/t.c" -I"${CVC_INSTALL_DIR}/include" -L"${CVC_INSTALL_DIR}/lib" -lsecp256k1
LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}" \
    "${T}/t"
echo "-- secp256k1 smoke test passed --"
