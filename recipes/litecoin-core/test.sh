#!/usr/bin/env bash
# recipes/litecoin-core/test.sh — smoke-test the Litecoin Core bundle.
#
# Starts litecoind -regtest with a throwaway datadir, an -rpcauth credential
# and a free loopback RPC port (P2P listening off), then over RPC:
# getblockchaininfo, createwallet (two legacy wallets), getnewaddress,
# generatetoaddress 101, getbalance, sendtoaddress + confirm,
# MWEB activation at height 432 with a peg-in, dumpwallet, stop; finally
# litecoin-wallet reads the wallet offline and litecoin-tx builds a
# transaction.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
P="${CVC_INSTALL_DIR}"

echo "-- litecoin-core smoke test (${CVC_PLATFORM:-native}) --"

for t in litecoind litecoin-cli litecoin-tx litecoin-wallet; do
    test -x "${P}/bin/${t}" || test -x "${P}/bin/${t}.exe" \
        || { echo "FAIL: bin/${t} missing"; exit 1; }
done
test -f "${P}/etc/litecoin/litecoin.conf.sample" || { echo "FAIL: litecoin.conf.sample missing"; exit 1; }
test -f "${P}/share/licenses/litecoin-core/COPYING" || { echo "FAIL: COPYING missing"; exit 1; }
test -f "${P}/share/licenses/litecoin-core/THIRD-PARTY-NOTICES.txt" \
    || { echo "FAIL: THIRD-PARTY-NOTICES.txt missing"; exit 1; }

# Windows binaries staged by a Linux-side cross job cannot run here, and the
# regtest walk below relies on POSIX paths/sockets; on Windows we stop at
# the version checks.
if [[ -n "${CVC_WINHOST:-}" ]]; then
    echo "  SKIP: Windows binaries staged on a Linux host"
    echo "-- litecoin-core smoke test passed --"
    exit 0
fi
"${P}/bin/litecoind" -version | sed -n 1p
"${P}/bin/litecoin-cli" -version | sed -n 1p
if [[ "${CVC_PLATFORM:-}" == "windows" ]]; then
    echo "  SKIP: regtest node walk on Windows (version checks only)"
    echo "-- litecoin-core smoke test passed --"
    exit 0
fi

T="$(mktemp -d "${TMPDIR:-/tmp}/ltc.XXXXXX")"
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

# rpcauth line for user "cvc", password "cvc-smoke-pw" (salted HMAC-SHA256,
# the format share/litecoin-core/rpcauth/rpcauth.py prints; no plaintext).
RPCAUTH='cvc:79ed0c9d1bffa8f097ea145624ad83f3$c0d1543496c053cf90a7f02d7955a267d8aa9b53f54cc8c7ea5bc4ddc5204068'

"${P}/bin/litecoind" -regtest -datadir="${T}" -server=1 -listen=0 -discover=0 \
    -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 -rpcport="${RPCPORT}" \
    -rpcauth="${RPCAUTH}" -fallbackfee=0.0002 -txindex=1 -printtoconsole=0 &
NODE_PID=$!

cli() {
    "${P}/bin/litecoin-cli" -regtest -datadir="${T}" -rpcport="${RPCPORT}" \
        -rpcuser=cvc -rpcpassword=cvc-smoke-pw "$@"
}

# litecoin-cli 0.21 has -rpcwait but no -rpcwaittimeout: poll instead.
info=""
for _ in $(seq 1 120); do
    kill -0 "${NODE_PID}" 2>/dev/null || { echo "FAIL: litecoind exited early"; exit 1; }
    if info="$(cli getblockchaininfo 2>/dev/null)"; then break; fi
    info=""; sleep 1
done
echo "${info}" | grep -q '"chain": "regtest"' || { echo "FAIL: getblockchaininfo: ${info}"; exit 1; }
echo "  OK: litecoind -regtest up, RPC on 127.0.0.1:${RPCPORT} (rpcauth)"

# Litecoin Core 0.21 wallets are legacy Berkeley DB wallets (descriptor
# wallets are disabled upstream).  Two wallets: a payer and a payee.
cli createwallet cvc > /dev/null
cli createwallet cvc-payee > /dev/null
cli -rpcwallet=cvc getwalletinfo | grep -q '"format": "bdb"' \
    || { echo "FAIL: wallet is not a legacy (bdb) wallet"; exit 1; }
echo "  OK: createwallet x2 (legacy bdb)"

ADDR="$(cli -rpcwallet=cvc getnewaddress)"
cli generatetoaddress 101 "${ADDR}" > /dev/null
height="$(cli getblockcount)"
[[ "${height}" == "101" ]] || { echo "FAIL: getblockcount=${height}"; exit 1; }
bal="$(cli -rpcwallet=cvc getbalance)"
[[ "${bal}" == "50.00000000" ]] || { echo "FAIL: getbalance=${bal}"; exit 1; }
echo "  OK: getnewaddress, generatetoaddress 101 -> balance ${bal}"

ADDR2="$(cli -rpcwallet=cvc-payee getnewaddress)"
TXID="$(cli -rpcwallet=cvc sendtoaddress "${ADDR2}" 1.5)"
cli generatetoaddress 1 "${ADDR}" > /dev/null
cli -rpcwallet=cvc gettransaction "${TXID}" | grep -q '"confirmations": 1' \
    || { echo "FAIL: ${TXID} not confirmed"; exit 1; }
bal2="$(cli -rpcwallet=cvc-payee getbalance)"
[[ "${bal2}" == "1.50000000" ]] || { echo "FAIL: payee balance=${bal2}"; exit 1; }
echo "  OK: sendtoaddress + 1 confirmation (${TXID}), recipient balance ${bal2}"

# MWEB: on regtest the soft fork activates at height 432, and that block can
# only be mined with a peg-in (a send to an mweb address) in the mempool.
cli generatetoaddress $(( 431 - 102 )) "${ADDR}" > /dev/null
MWEB_ADDR="$(cli -rpcwallet=cvc getnewaddress "" mweb)"
[[ "${MWEB_ADDR}" == tmweb1* ]] || { echo "FAIL: mweb address ${MWEB_ADDR}"; exit 1; }
PEGIN="$(cli -rpcwallet=cvc sendtoaddress "${MWEB_ADDR}" 2)"
cli generatetoaddress 1 "${ADDR}" > /dev/null
height="$(cli getblockcount)"
[[ "${height}" == "432" ]] || { echo "FAIL: MWEB activation block not mined (height ${height})"; exit 1; }
cli getblockchaininfo | tr -d ' \n' | grep -q '"mweb":{[^}]*"status":"active"' \
    || { echo "FAIL: MWEB not active at 432"; exit 1; }
cli -rpcwallet=cvc gettransaction "${PEGIN}" | grep -q '"confirmations": 1' \
    || { echo "FAIL: peg-in ${PEGIN} not confirmed"; exit 1; }
echo "  OK: MWEB active at height 432, peg-in to ${MWEB_ADDR:0:16}... confirmed"

cli -rpcwallet=cvc dumpwallet "${T}/cvc-dump.txt" > /dev/null
grep -q "${ADDR}" "${T}/cvc-dump.txt" || { echo "FAIL: dumpwallet"; exit 1; }
echo "  OK: dumpwallet (legacy wallet)"

cli stop > /dev/null
for _ in $(seq 1 60); do kill -0 "${NODE_PID}" 2>/dev/null || break; sleep 1; done
if kill -0 "${NODE_PID}" 2>/dev/null; then echo "FAIL: litecoind did not stop"; exit 1; fi
wait "${NODE_PID}" 2>/dev/null || true
NODE_PID=""
echo "  OK: litecoin-cli stop"

"${P}/bin/litecoin-wallet" -regtest -datadir="${T}" -wallet=cvc info | grep -q "Wallet info" \
    || { echo "FAIL: litecoin-wallet info"; exit 1; }
echo "  OK: litecoin-wallet info"
rawtx="$("${P}/bin/litecoin-tx" -create nversion=2)"
[[ -n "${rawtx}" ]] || { echo "FAIL: litecoin-tx -create"; exit 1; }
echo "  OK: litecoin-tx -create -> ${rawtx}"

echo "-- litecoin-core smoke test passed --"
