#!/usr/bin/env bash
# recipes/bitcoin-core/test.sh — smoke-test the Bitcoin Core bundle.
#
# Starts bitcoind -regtest with a throwaway datadir, an -rpcauth credential
# and a free loopback RPC port (P2P listening off), then over RPC:
# getblockchaininfo, createwallet, getnewaddress, generatetoaddress 101,
# getbalance, sendtoaddress + confirm, stop; finally bitcoin-wallet reads the
# wallet offline and bitcoin-tx builds a transaction.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
P="${CVC_INSTALL_DIR}"

echo "-- bitcoin-core smoke test (${CVC_PLATFORM:-native}) --"

for t in bitcoind bitcoin-cli bitcoin-tx bitcoin-util bitcoin-wallet bitcoin; do
    test -x "${P}/bin/${t}" || test -x "${P}/bin/${t}.exe" \
        || { echo "FAIL: bin/${t} missing"; exit 1; }
done
test -f "${P}/etc/bitcoin/bitcoin.conf.sample" || { echo "FAIL: bitcoin.conf.sample missing"; exit 1; }
test -f "${P}/share/licenses/bitcoin-core/COPYING" || { echo "FAIL: COPYING missing"; exit 1; }

# Windows binaries staged by a Linux-side cross job cannot run here, and the
# regtest walk below relies on POSIX paths/sockets; on Windows we stop at
# the version checks.
if [[ -n "${CVC_WINHOST:-}" ]]; then
    echo "  SKIP: Windows binaries staged on a Linux host"
    echo "-- bitcoin-core smoke test passed --"
    exit 0
fi
"${P}/bin/bitcoind" -version | sed -n 1p
"${P}/bin/bitcoin-cli" -version | sed -n 1p
if [[ "${CVC_PLATFORM:-}" == "windows" ]]; then
    echo "  SKIP: regtest node walk on Windows (version checks only)"
    echo "-- bitcoin-core smoke test passed --"
    exit 0
fi

T="$(mktemp -d "${TMPDIR:-/tmp}/btc.XXXXXX")"
NODE_PID=""
cleanup() {
    local rc=$?
    if [[ -n "${NODE_PID}" ]] && kill -0 "${NODE_PID}" 2>/dev/null; then
        kill "${NODE_PID}" 2>/dev/null || true
        for _ in $(seq 1 30); do kill -0 "${NODE_PID}" 2>/dev/null || break; sleep 1; done
        kill -9 "${NODE_PID}" 2>/dev/null || true
    fi
    if [[ ${rc} -ne 0 && -f "${T}/regtest/debug.log" ]]; then
        echo "---- regtest/debug.log (tail) ----"; tail -n 30 "${T}/regtest/debug.log" || true
    fi
    rm -rf "${T}"
    exit "${rc}"
}
trap cleanup EXIT

RPCPORT=""
for _ in $(seq 1 50); do
    cand=$(( 20000 + (RANDOM % 30000) ))
    if ! (exec 3<>"/dev/tcp/127.0.0.1/${cand}") 2>/dev/null; then RPCPORT="${cand}"; break; fi
done
[[ -n "${RPCPORT}" ]] || { echo "FAIL: no free port"; exit 1; }

# rpcauth line for user "cvc", password "cvc-smoke-pw", made with
# share/bitcoin-core/rpcauth/rpcauth.py (salted HMAC-SHA256, no plaintext).
RPCAUTH='cvc:79ed0c9d1bffa8f097ea145624ad83f3$c0d1543496c053cf90a7f02d7955a267d8aa9b53f54cc8c7ea5bc4ddc5204068'

"${P}/bin/bitcoind" -regtest -datadir="${T}" -server=1 -listen=0 -discover=0 \
    -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 -rpcport="${RPCPORT}" \
    -rpcauth="${RPCAUTH}" -fallbackfee=0.0002 -txindex=1 -printtoconsole=0 &
NODE_PID=$!

cli() {
    "${P}/bin/bitcoin-cli" -regtest -datadir="${T}" -rpcport="${RPCPORT}" \
        -rpcuser=cvc -rpcpassword=cvc-smoke-pw "$@"
}

info="$(cli -rpcwait -rpcwaittimeout=120 getblockchaininfo)"
echo "${info}" | grep -q '"chain": "regtest"' || { echo "FAIL: getblockchaininfo: ${info}"; exit 1; }
echo "  OK: bitcoind -regtest up, RPC on 127.0.0.1:${RPCPORT} (rpcauth)"

cli createwallet cvc > /dev/null
ADDR="$(cli -rpcwallet=cvc getnewaddress)"
cli generatetoaddress 101 "${ADDR}" > /dev/null
height="$(cli getblockcount)"
[[ "${height}" == "101" ]] || { echo "FAIL: getblockcount=${height}"; exit 1; }
bal="$(cli -rpcwallet=cvc getbalance)"
[[ "${bal}" == "50.00000000" ]] || { echo "FAIL: getbalance=${bal}"; exit 1; }
echo "  OK: createwallet, getnewaddress, generatetoaddress 101 -> balance ${bal}"

ADDR2="$(cli -rpcwallet=cvc getnewaddress)"
TXID="$(cli -rpcwallet=cvc sendtoaddress "${ADDR2}" 1.5)"
cli generatetoaddress 1 "${ADDR}" > /dev/null
cli -rpcwallet=cvc gettransaction "${TXID}" | grep -q '"confirmations": 1' \
    || { echo "FAIL: ${TXID} not confirmed"; exit 1; }
echo "  OK: sendtoaddress + 1 confirmation (${TXID})"

# The `bitcoin` front-end dispatches to the same tools.
"${P}/bin/bitcoin" rpc -regtest -datadir="${T}" -rpcport="${RPCPORT}" \
    -rpcuser=cvc -rpcpassword=cvc-smoke-pw getblockcount | grep -qx 102 \
    || { echo "FAIL: bitcoin rpc getblockcount"; exit 1; }
echo "  OK: bitcoin rpc front-end"

cli stop > /dev/null
for _ in $(seq 1 60); do kill -0 "${NODE_PID}" 2>/dev/null || break; sleep 1; done
if kill -0 "${NODE_PID}" 2>/dev/null; then echo "FAIL: bitcoind did not stop"; exit 1; fi
wait "${NODE_PID}" 2>/dev/null || true
NODE_PID=""
echo "  OK: bitcoin-cli stop"

"${P}/bin/bitcoin-wallet" -regtest -datadir="${T}" -wallet=cvc info | grep -q "Wallet info" \
    || { echo "FAIL: bitcoin-wallet info"; exit 1; }
echo "  OK: bitcoin-wallet info"
rawtx="$("${P}/bin/bitcoin-tx" -create nversion=2)"
[[ -n "${rawtx}" ]] || { echo "FAIL: bitcoin-tx -create"; exit 1; }
echo "  OK: bitcoin-tx -create -> ${rawtx}"

echo "-- bitcoin-core smoke test passed --"
