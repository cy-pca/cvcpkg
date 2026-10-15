#!/usr/bin/env bash
# recipes/dogecoin-core/test.sh — smoke-test the Dogecoin Core bundle.
#
# Starts dogecoind -regtest with a throwaway datadir, an -rpcauth credential
# and a free loopback RPC port (P2P listening off), then over RPC — using
# what Dogecoin Core 1.14 (a Bitcoin Core 0.14 descendant) supports:
# getblockchaininfo, getnewaddress on the single legacy wallet,
# generatetoaddress 101 and the old `generate`, getbalance, validateaddress,
# sendtoaddress + confirm, getreceivedbyaddress, dumpwallet, stop; finally
# dogecoin-tx builds a transaction.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
P="${CVC_INSTALL_DIR}"

echo "-- dogecoin-core smoke test (${CVC_PLATFORM:-native}) --"

for t in dogecoind dogecoin-cli dogecoin-tx; do
    test -x "${P}/bin/${t}" || test -x "${P}/bin/${t}.exe" \
        || { echo "FAIL: bin/${t} missing"; exit 1; }
done
test -f "${P}/etc/dogecoin/dogecoin.conf.sample" || { echo "FAIL: dogecoin.conf.sample missing"; exit 1; }
test -f "${P}/share/licenses/dogecoin-core/COPYING" || { echo "FAIL: COPYING missing"; exit 1; }
test -f "${P}/share/licenses/dogecoin-core/THIRD-PARTY-NOTICES.txt" \
    || { echo "FAIL: THIRD-PARTY-NOTICES.txt missing"; exit 1; }

# Windows binaries staged by a Linux-side cross job cannot run here, and the
# regtest walk below relies on POSIX paths/sockets; on Windows we stop at
# the version checks.
if [[ -n "${CVC_WINHOST:-}" ]]; then
    echo "  SKIP: Windows binaries staged on a Linux host"
    echo "-- dogecoin-core smoke test passed --"
    exit 0
fi
"${P}/bin/dogecoind" -version | sed -n 1p
"${P}/bin/dogecoin-cli" -version | sed -n 1p
if [[ "${CVC_PLATFORM:-}" == "windows" ]]; then
    echo "  SKIP: regtest node walk on Windows (version checks only)"
    echo "-- dogecoin-core smoke test passed --"
    exit 0
fi

T="$(mktemp -d "${TMPDIR:-/tmp}/doge.XXXXXX")"
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
# the format share/dogecoin-core/rpcuser/rpcuser.py prints; no plaintext).
RPCAUTH='cvc:79ed0c9d1bffa8f097ea145624ad83f3$c0d1543496c053cf90a7f02d7955a267d8aa9b53f54cc8c7ea5bc4ddc5204068'

"${P}/bin/dogecoind" -regtest -datadir="${T}" -server=1 -listen=0 -discover=0 \
    -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 -rpcport="${RPCPORT}" \
    -rpcauth="${RPCAUTH}" -txindex=1 -printtoconsole=0 &
NODE_PID=$!

cli() {
    "${P}/bin/dogecoin-cli" -regtest -datadir="${T}" -rpcport="${RPCPORT}" \
        -rpcuser=cvc -rpcpassword=cvc-smoke-pw "$@"
}

# dogecoin-cli 1.14 has -rpcwait but no timeout for it: poll instead.
info=""
for _ in $(seq 1 120); do
    kill -0 "${NODE_PID}" 2>/dev/null || { echo "FAIL: dogecoind exited early"; exit 1; }
    if info="$(cli getblockchaininfo 2>/dev/null)"; then break; fi
    info=""; sleep 1
done
echo "${info}" | grep -q '"chain": "regtest"' || { echo "FAIL: getblockchaininfo: ${info}"; exit 1; }
echo "  OK: dogecoind -regtest up, RPC on 127.0.0.1:${RPCPORT} (rpcauth)"

# 1.14 has one legacy wallet (wallet.dat) and no createwallet.
ADDR="$(cli getnewaddress)"
cli validateaddress "${ADDR}" | grep -q '"isvalid": true' || { echo "FAIL: validateaddress ${ADDR}"; exit 1; }
cli generatetoaddress 101 "${ADDR}" > /dev/null
height="$(cli getblockcount)"
[[ "${height}" == "101" ]] || { echo "FAIL: getblockcount=${height}"; exit 1; }
# Regtest: 500,000 DOGE per block, coinbase maturity 60 -> 41 mature blocks.
bal="$(cli getbalance)"
[[ "${bal}" == "20500000.00000000" ]] || { echo "FAIL: getbalance=${bal}"; exit 1; }
echo "  OK: getnewaddress, generatetoaddress 101 -> balance ${bal}"
cli generate 1 > /dev/null
height="$(cli getblockcount)"
[[ "${height}" == "102" ]] || { echo "FAIL: generate 1 -> height ${height}"; exit 1; }
echo "  OK: generate 1 (pre-0.18 API) -> height ${height}"

ADDR2="$(cli getnewaddress)"
TXID="$(cli sendtoaddress "${ADDR2}" 1000)"
cli generatetoaddress 1 "${ADDR}" > /dev/null
cli gettransaction "${TXID}" | grep -q '"confirmations": 1' \
    || { echo "FAIL: ${TXID} not confirmed"; exit 1; }
recv="$(cli getreceivedbyaddress "${ADDR2}" 1)"
[[ "${recv}" == "1000.00000000" ]] || { echo "FAIL: getreceivedbyaddress=${recv}"; exit 1; }
echo "  OK: sendtoaddress + 1 confirmation (${TXID}), received ${recv}"

# 1.14.x writes dumps only to <datadir>/<chain>/backups/<basename>.
cli dumpwallet cvc-dump > /dev/null
grep -q "${ADDR}" "${T}/regtest/backups/cvc-dump" || { echo "FAIL: dumpwallet"; exit 1; }
echo "  OK: dumpwallet -> regtest/backups/cvc-dump"

cli stop > /dev/null
for _ in $(seq 1 60); do kill -0 "${NODE_PID}" 2>/dev/null || break; sleep 1; done
if kill -0 "${NODE_PID}" 2>/dev/null; then echo "FAIL: dogecoind did not stop"; exit 1; fi
wait "${NODE_PID}" 2>/dev/null || true
NODE_PID=""
echo "  OK: dogecoin-cli stop"

rawtx="$("${P}/bin/dogecoin-tx" -create nversion=1)"
[[ -n "${rawtx}" ]] || { echo "FAIL: dogecoin-tx -create"; exit 1; }
echo "  OK: dogecoin-tx -create -> ${rawtx}"

echo "-- dogecoin-core smoke test passed --"
