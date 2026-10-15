#!/usr/bin/env bash
# recipes/valkey/test.sh — start the built valkey-server on loopback and talk
# to it through valkey-cli and the redis-cli compatibility name.
#
# Invoked by the packager after build.sh populates $CVC_INSTALL_DIR (it also
# puts the deps prefix's lib/ on the loader path).  The EXIT trap stops the
# server on success, failure or interrupt, so a failed test never leaves a
# stray valkey-server behind on the builder.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
BIN="${CVC_INSTALL_DIR}/bin"

echo "-- valkey smoke test (${CVC_PLATFORM:-native}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 1. Programs, and the redis-* names as relative links inside bin/.
for n in server cli benchmark check-aof check-rdb sentinel; do
    [[ -x "${BIN}/valkey-${n}" ]] || fail "bin/valkey-${n} missing or not executable"
    link="${BIN}/redis-${n}"
    [[ -L "${link}" ]] || fail "bin/redis-${n} is not a symlink"
    target=$(readlink "${link}")
    [[ "${target}" != */* ]] || fail "bin/redis-${n} -> ${target} is not a relative same-directory link"
    [[ -x "${link}" ]] || fail "bin/redis-${n} -> ${target} dangles"
done
echo "  OK: valkey-* programs and relative redis-* links present"
"${BIN}/valkey-server" --version
"${BIN}/redis-sentinel" --version | grep -q '^Valkey server v=' || fail "redis-sentinel --version"

# 2. Start a server on a free loopback port, with TLS on a second one when an
#    openssl CLI is around to mint a throwaway certificate.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/valkey-test.XXXXXX")
SERVER_PID=""
cleanup() {
    local rc=$?
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill -TERM "${SERVER_PID}" 2>/dev/null || true
        for ((i = 0; i < 50; i++)); do
            kill -0 "${SERVER_PID}" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    if [[ ${rc} -ne 0 && -f "${WORK}/server.log" ]]; then
        # A crash report ends in pages of INFO/CONFIG dump; print the cause
        # (warnings, assertion, panic) last so it survives a tail of this log.
        echo "--- server log (tail) ---" >&2
        tail -n 10 "${WORK}/server.log" >&2 || true
        echo "--- server log: warnings and crash cause ---" >&2
        grep -E ' # |ASSERTION|crashed by signal|Guru Meditation' "${WORK}/server.log" \
            | head -n 20 >&2 || true
    fi
    rm -rf "${WORK}"
    exit "${rc}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

TLS=0
if command -v openssl >/dev/null 2>&1 \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=127.0.0.1" \
        -addext "subjectAltName=IP:127.0.0.1" \
        -keyout "${WORK}/tls.key" -out "${WORK}/tls.crt" >/dev/null 2>&1; then
    TLS=1
else
    echo "  WARN: no usable openssl CLI; skipping the TLS check"
fi

cli() { "${BIN}/valkey-cli" -h 127.0.0.1 -p "${PORT}" "$@"; }

random_port() { echo $((20000 + (RANDOM * 32768 + RANDOM) % 40000)); }

for attempt in 1 2 3 4 5 6 7 8; do
    PORT=$(random_port)
    args=(--bind 127.0.0.1 --port "${PORT}" --save '' --appendonly no
          --daemonize no --dir "${WORK}")
    if [[ ${TLS} -eq 1 ]]; then
        TLS_PORT=$(random_port)
        args+=(--tls-port "${TLS_PORT}" --tls-cert-file "${WORK}/tls.crt"
               --tls-key-file "${WORK}/tls.key" --tls-ca-cert-file "${WORK}/tls.crt"
               --tls-auth-clients no)
    fi
    "${BIN}/valkey-server" "${args[@]}" >"${WORK}/server.log" 2>&1 &
    SERVER_PID=$!
    for ((i = 0; i < 100; i++)); do
        kill -0 "${SERVER_PID}" 2>/dev/null || break
        [[ "$(cli ping 2>/dev/null)" == PONG ]] && break 2
        sleep 0.1
    done
    kill -0 "${SERVER_PID}" 2>/dev/null && fail "valkey-server did not answer PING within 10s"
    wait "${SERVER_PID}" 2>/dev/null || true
    SERVER_PID=""
    grep -q 'Address already in use' "${WORK}/server.log" \
        || fail "valkey-server exited during startup"
    echo "  port clash on attempt ${attempt}; retrying"
done
[[ -n "${SERVER_PID}" ]] || fail "no free port found"
echo "  OK: valkey-server up on 127.0.0.1:${PORT} (pid ${SERVER_PID})"

# 3. What the consuming apps rely on: PING through both CLI names (their
#    health checks run `redis-cli ping`), plain commands, Lua EVAL.
expect() {  # expect <want> <what> <cmd...>
    local want=$1 what=$2 got
    shift 2
    got=$("$@") || fail "${what}: command failed"
    [[ "${got}" == "${want}" ]] || fail "${what}: expected '${want}', got '${got}'"
    echo "  OK: ${what} -> ${got}"
}
expect PONG "valkey-cli ping" cli ping
expect PONG "redis-cli ping" "${BIN}/redis-cli" -h 127.0.0.1 -p "${PORT}" ping
expect OK "SET" cli set cvc:greeting hello
expect hello "GET" cli get cvc:greeting
expect 0 "EVAL exists(nokey)" cli eval "return redis.call('exists', KEYS[1])" 1 nokey
expect world "EVAL write+read" \
    cli eval "redis.call('set', KEYS[1], ARGV[1]); return redis.call('get', KEYS[1])" 1 cvc:lua world
cli info server | grep -q '^server_name:valkey' || fail "INFO server lacks server_name:valkey"
# -q still redraws a progress line with \r; keep only the summaries.
"${BIN}/valkey-benchmark" -h 127.0.0.1 -p "${PORT}" -c 4 -n 2000 -t set,get -q 2>&1 \
    | tr '\r' '\n' | grep 'requests per second' | sed 's/^ */  OK: valkey-benchmark /' \
    || fail "valkey-benchmark"

if [[ ${TLS} -eq 1 ]]; then
    expect PONG "valkey-cli --tls ping" \
        "${BIN}/valkey-cli" --tls --cacert "${WORK}/tls.crt" -h 127.0.0.1 -p "${TLS_PORT}" ping
fi

# 4. Snapshot, stop the server cleanly, and read the snapshot back with
#    valkey-check-rdb (an argv[0] personality of valkey-server).
expect OK "SAVE" cli save
cli shutdown nosave >/dev/null 2>&1 || true
rc=0
wait "${SERVER_PID}" || rc=$?
SERVER_PID=""
[[ ${rc} -eq 0 ]] || fail "valkey-server exited with status ${rc} after SHUTDOWN"
echo "  OK: SHUTDOWN NOSAVE, server exited 0"
"${BIN}/redis-check-rdb" "${WORK}/dump.rdb" | grep -q 'RDB looks OK' \
    || fail "redis-check-rdb did not accept the server's own dump.rdb"
echo "  OK: redis-check-rdb accepts the snapshot"

echo "-- valkey smoke test passed --"
