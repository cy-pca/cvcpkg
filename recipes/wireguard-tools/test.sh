#!/usr/bin/env bash
# recipes/wireguard-tools/test.sh — smoke test the wireguard-tools install.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- wireguard-tools smoke test (${CVC_PLATFORM:-native}) --"

test -x "${CVC_INSTALL_DIR}/bin/wg" \
    || { echo "FAIL: bin/wg missing or not executable"; exit 1; }
test -f "${CVC_INSTALL_DIR}/bin/wg-quick" \
    || { echo "FAIL: bin/wg-quick missing"; exit 1; }
echo "  OK: wg + wg-quick present"

# `wg --version` prints "wireguard-tools vX.Y.Z" and exits 0 without needing
# any interface or privileges.
ver="$("${CVC_INSTALL_DIR}/bin/wg" --version 2>&1 || true)"
echo "  ${ver}"
case "${ver}" in
    *wireguard-tools*) echo "  OK: wg runs" ;;
    *) echo "FAIL: unexpected 'wg --version' output"; exit 1 ;;
esac

# wg-quick is a shell script; check it parses and reports usage.
if command -v bash >/dev/null 2>&1; then
    bash -n "${CVC_INSTALL_DIR}/bin/wg-quick" \
        && echo "  OK: wg-quick parses" \
        || { echo "FAIL: wg-quick has a syntax error"; exit 1; }
fi

echo "-- wireguard-tools smoke test passed --"
