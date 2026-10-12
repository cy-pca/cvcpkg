#!/usr/bin/env bash
# recipes/rclone/test.sh — exercise bin/rclone: version, a checksum-verified
# local -> local sync + `rclone check`, and (when a SeaweedFS `weed` binary is
# available) an S3 round trip configured purely from RCLONE_CONFIG_* env vars.
#
# The S3 part runs when `weed` is found via $RCLONE_TEST_WEED, the build/deps
# prefixes or PATH (install the cvcpkg `seaweedfs` package to get it), and is
# skipped otherwise; set RCLONE_TEST_REQUIRE_S3=1 to make a missing weed fail.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
RCLONE="${CVC_INSTALL_DIR}/bin/rclone"

echo "-- rclone smoke test (${CVC_PLATFORM:-native}) --"

ver="$("${RCLONE}" version)"
while IFS= read -r l; do echo "  ${l}"; done <<< "${ver}"
case "$(echo "${ver}" | head -1)" in
    "rclone v1.75.2") ;;
    *) echo "FAIL: expected 'rclone v1.75.2' as the first line"; exit 1 ;;
esac

T="$(mktemp -d)"
PIDS=""
running() {  # $1 = pid; an exited-but-unreaped child (zombie) does not count
    kill -0 "$1" 2>/dev/null && ! ps -o stat= -p "$1" 2>/dev/null | grep -q "^Z"
}
stop_all() {  # SIGTERM, up to 20s, then SIGKILL (weed filer -s3 can hang on exit)
    local e alive
    for e in ${PIDS}; do kill "${e#*:}" 2>/dev/null || true; done
    for _ in $(seq 1 40); do
        alive=""
        for e in ${PIDS}; do if running "${e#*:}"; then alive="${alive} ${e}"; fi; done
        [ -z "${alive}" ] && break
        sleep 0.5
    done
    for e in ${PIDS}; do
        if running "${e#*:}"; then kill -9 "${e#*:}" 2>/dev/null || true; fi
    done
    for e in ${PIDS}; do wait "${e#*:}" 2>/dev/null || true; done
    PIDS=""
}
cleanup() {
    [ -z "${PIDS}" ] || stop_all
    rm -rf "${T}"
}
trap cleanup EXIT

# Everything rclone might write (config, cache, logs) stays in the temp dir,
# and no config file is used at all: remotes come from RCLONE_CONFIG_* vars.
export HOME="${T}/home" TMPDIR="${T}/tmp" XDG_CONFIG_HOME="${T}/xdg-config" XDG_CACHE_HOME="${T}/xdg-cache"
# An empty RCLONE_CONFIG means "in-memory configuration only".
export RCLONE_CONFIG="" RCLONE_CACHE_DIR="${T}/rclone-cache"
mkdir -p "${HOME}" "${TMPDIR}"
no_config_written() {
    [ -z "$(find "${T}" -name 'rclone.conf' -print -quit)" ]
}
case "$("${RCLONE}" config file 2>&1)" in
    *"in memory only"*) ;;
    *) echo "FAIL: RCLONE_CONFIG= did not give an in-memory configuration"; exit 1 ;;
esac

# ── local -> local ─────────────────────────────────────────────────────
SRC="${T}/src"; DST="${T}/dst"
mkdir -p "${SRC}/db/2026-10" "${SRC}/media/ünïcødé dir" "${SRC}/empty-dir"
printf 'alpha\n' > "${SRC}/a.txt"
: > "${SRC}/zero-length"
head -c 3000000 /dev/urandom > "${SRC}/db/2026-10/dump.sql.gz"
printf 'file with spaces and unicode\n' > "${SRC}/media/ünïcødé dir/naïve file.txt"
printf 'stale\n' > "${T}/stale-only-in-dst"

"${RCLONE}" sync --checksum "${SRC}" "${DST}"
cp "${T}/stale-only-in-dst" "${DST}/stale-only-in-dst"
"${RCLONE}" sync --checksum "${SRC}" "${DST}"
[ ! -e "${DST}/stale-only-in-dst" ] || { echo "FAIL: sync did not delete an extra file in the destination"; exit 1; }
"${RCLONE}" check "${SRC}" "${DST}"
# Independent of rclone: same file list, same bytes. (Empty directories are
# not copied without --create-empty-src-dirs, so compare files only.)
(cd "${SRC}" && find . -type f | sort) > "${T}/s.lst"
(cd "${DST}" && find . -type f | sort) > "${T}/d.lst"
diff "${T}/s.lst" "${T}/d.lst"
while read -r f; do cmp "${SRC}/${f}" "${DST}/${f}"; done < "${T}/s.lst"
echo "  OK: local sync --checksum + rclone check (incl. deletion of an extra file)"

# Same size AND same modification time, different content: only a checksum
# comparison notices. `rclone check` must flag it, and sync --checksum fix it.
printf 'ALPHA\n' > "${SRC}/a.txt"
touch -r "${DST}/a.txt" "${SRC}/a.txt"
if "${RCLONE}" check "${SRC}" "${DST}" >/dev/null 2>&1; then
    echo "FAIL: rclone check missed a same-size, same-mtime content change"; exit 1
fi
"${RCLONE}" sync --checksum "${SRC}" "${DST}"
"${RCLONE}" check "${SRC}" "${DST}"
cmp "${SRC}/a.txt" "${DST}/a.txt"
echo "  OK: --checksum caught a same-size/same-mtime change; check passes again"
no_config_written || { echo "FAIL: rclone wrote an rclone.conf"; exit 1; }

# ── optional S3 round trip against SeaweedFS ───────────────────────────
WEED="${RCLONE_TEST_WEED:-}"
for d in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    if [ -z "${WEED}" ] && [ -n "${d}" ] && [ -x "${d}/bin/weed" ]; then WEED="${d}/bin/weed"; fi
done
[ -n "${WEED}" ] || WEED="$(command -v weed || true)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "${WEED}" ] || [ -z "${PY}" ]; then
    if [ "${RCLONE_TEST_REQUIRE_S3:-0}" = 1 ]; then
        echo "FAIL: RCLONE_TEST_REQUIRE_S3=1 but no weed (or python3) found"; exit 1
    fi
    echo "  SKIP: S3 round trip (no SeaweedFS 'weed' binary or python3 on this builder)"
    echo "-- rclone smoke test passed --"
    exit 0
fi
echo "  S3 target: $("${WEED}" version 2>/dev/null | head -1) (${WEED})"

# Four HTTP ports whose gRPC companion (port+10000) is free too; see the
# seaweedfs recipe's test.sh for why the gRPC ports stay implicit.
read -r M V F S < <("${PY}" -I -c '
import random, socket
def free(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p)); return True
    except OSError:
        return False
    finally:
        s.close()
ports = []
while len(ports) < 4:
    p = random.randint(20000, 54999)
    if all(abs(p - q) > 1 and abs(p - q) != 10000 for q in ports) and free(p) and free(p + 10000):
        ports.append(p)
print(*ports)
')
AK="CVCPKGRCLONETEST0001"
SK="cvcpkg-rclone-secret-$(date +%s)-$$"
cat > "${T}/s3.json" <<EOF
{"identities": [{"name": "cvcpkg-rclone-test",
  "credentials": [{"accessKey": "${AK}", "secretKey": "${SK}"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
EOF
mkdir -p "${T}/m" "${T}/v" "${T}/f"
(
    cd "${T}"
    COMMON="-ip=127.0.0.1 -ip.bind=127.0.0.1"
    # shellcheck disable=SC2086
    exec "${WEED}" master ${COMMON} -port="${M}" -mdir="${T}/m" -volumeSizeLimitMB=64
) > "${T}/master.log" 2>&1 &
PIDS="${PIDS} master:$!"
(
    cd "${T}"
    COMMON="-ip=127.0.0.1 -ip.bind=127.0.0.1"
    # shellcheck disable=SC2086
    exec "${WEED}" volume ${COMMON} -port="${V}" -dir="${T}/v" -max=8 -master="127.0.0.1:${M}"
) > "${T}/volume.log" 2>&1 &
PIDS="${PIDS} volume:$!"
(
    cd "${T}"
    COMMON="-ip=127.0.0.1 -ip.bind=127.0.0.1"
    # shellcheck disable=SC2086
    exec "${WEED}" filer ${COMMON} -port="${F}" -master="127.0.0.1:${M}" \
        -defaultStoreDir="${T}/f" -localSocket="${T}/filer.sock" \
        -s3 -s3.port="${S}" -s3.config="${T}/s3.json" \
        -s3.localSocket="${T}/s3.sock" -s3.port.iceberg=0 -s3.port.lance=0
) > "${T}/filer.log" 2>&1 &
PIDS="${PIDS} filer:$!"

# The remote "sw" exists only in the environment: no config file is written.
export RCLONE_CONFIG_SW_TYPE=s3
export RCLONE_CONFIG_SW_PROVIDER=SeaweedFS
export RCLONE_CONFIG_SW_ENDPOINT="http://127.0.0.1:${S}"
export RCLONE_CONFIG_SW_REGION=us-east-1
export RCLONE_CONFIG_SW_FORCE_PATH_STYLE=true
export RCLONE_CONFIG_SW_ACCESS_KEY_ID="${AK}"
export RCLONE_CONFIG_SW_SECRET_ACCESS_KEY="${SK}"
S3FLAGS="--retries 1 --low-level-retries 2 --contimeout 5s --timeout 30s"

fail_s3() {
    echo "FAIL: $*"
    for l in master volume filer; do echo "--- ${l}.log (tail)"; tail -20 "${T}/${l}.log"; done
    exit 1
}
ready=0
for _ in $(seq 1 180); do
    # shellcheck disable=SC2086
    if "${RCLONE}" lsd sw: ${S3FLAGS} >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[ "${ready}" = 1 ] || fail_s3 "S3 gateway not ready after 180s"
echo "  OK: SeaweedFS S3 up on 127.0.0.1:${S}; rclone lsd sw: works"

# shellcheck disable=SC2086
if RCLONE_CONFIG_SW_SECRET_ACCESS_KEY="${SK}-wrong" "${RCLONE}" lsd sw: ${S3FLAGS} >/dev/null 2>&1; then
    fail_s3 "a wrong secret key was accepted"
fi
echo "  OK: wrong secret key refused"

B="cvcpkg-rclone-test"
R="sw:${B}/backups/host-1"
# shellcheck disable=SC2086
"${RCLONE}" mkdir "sw:${B}" ${S3FLAGS} || fail_s3 "mkdir sw:${B}"
ok=0
for _ in $(seq 1 60); do   # the first write waits for the volume server
    # shellcheck disable=SC2086
    if "${RCLONE}" sync --checksum "${SRC}" "${R}" ${S3FLAGS} 2>"${T}/sync.err"; then ok=1; break; fi
    sleep 2
done
[ "${ok}" = 1 ] || fail_s3 "sync to S3: $(tail -5 "${T}/sync.err")"
# shellcheck disable=SC2086
"${RCLONE}" check "${SRC}" "${R}" ${S3FLAGS} || fail_s3 "rclone check local vs S3"
echo "  OK: sync --checksum to S3 + rclone check"

rm "${SRC}/a.txt"
# shellcheck disable=SC2086
"${RCLONE}" sync --checksum "${SRC}" "${R}" ${S3FLAGS} || fail_s3 "second sync"
# shellcheck disable=SC2086
if "${RCLONE}" lsf "${R}" ${S3FLAGS} | grep -qx "a.txt"; then fail_s3 "deleted file still on S3"; fi
mkdir -p "${T}/back"
# shellcheck disable=SC2086
"${RCLONE}" copy "${R}" "${T}/back" ${S3FLAGS} || fail_s3 "copy back from S3"
while read -r f; do
    [ "${f}" = "./a.txt" ] && continue
    cmp "${SRC}/${f}" "${T}/back/${f}" || fail_s3 "round-tripped ${f} differs"
done < "${T}/s.lst"
echo "  OK: deletion propagated; copy back from S3 is byte-identical"
# shellcheck disable=SC2086
"${RCLONE}" purge "sw:${B}" ${S3FLAGS} || fail_s3 "purge"
no_config_written || fail_s3 "rclone wrote an rclone.conf"
echo "  OK: bucket purged; no rclone config file was written"

stop_all
echo "-- rclone smoke test passed --"
