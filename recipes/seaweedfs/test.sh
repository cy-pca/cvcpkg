#!/usr/bin/env bash
# recipes/seaweedfs/test.sh — run a single-node SeaweedFS and use its S3 API.
#
# master + volume + filer(+S3) as three local processes on 127.0.0.1 with free
# ports, everything (data, metadata, logs, unix sockets) inside a temp dir.
# The S3 gateway gets one identity via -s3.config; the test then checks that
#   * unsigned requests and a wrong secret are refused (403), and
#   * SigV4-signed requests create a bucket, put an object and get it back.
# SigV4 is done with Python's stdlib (hmac/hashlib), no AWS SDK needed.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
WEED="${CVC_INSTALL_DIR}/bin/weed"

echo "-- seaweedfs smoke test (${CVC_PLATFORM:-native}) --"

PY="$(command -v python3 || command -v python || true)"
[ -n "${PY}" ] || { echo "FAIL: python3 is required for the S3 round trip"; exit 1; }

ver="$("${WEED}" version)"
echo "  ${ver}"
case "${ver}" in *"4.48"*) ;; *) echo "FAIL: expected weed 4.48"; exit 1 ;; esac

T="$(mktemp -d)"
PIDS=""
# SIGTERM, up to 20s to exit, then SIGKILL. In 4.48 `weed filer -s3` does not
# always finish its graceful shutdown, and a test must never leave a server
# behind. Leaves the names of the ones that needed SIGKILL in KILLED.
running() {  # $1 = pid; an exited-but-unreaped child (zombie) does not count
    kill -0 "$1" 2>/dev/null && ! ps -o stat= -p "$1" 2>/dev/null | grep -q "^Z"
}
stop_all() {  # PIDS holds name:pid entries
    local e n alive
    KILLED=""
    for e in ${PIDS}; do kill "${e#*:}" 2>/dev/null || true; done
    for n in $(seq 1 40); do
        alive=""
        for e in ${PIDS}; do if running "${e#*:}"; then alive="${alive} ${e}"; fi; done
        [ -z "${alive}" ] && break
        sleep 0.5
    done
    for e in ${PIDS}; do
        if running "${e#*:}"; then
            kill -9 "${e#*:}" 2>/dev/null || true
            KILLED="${KILLED} ${e%%:*}"
        fi
    done
    for e in ${PIDS}; do wait "${e#*:}" 2>/dev/null || true; done
    PIDS=""
}
cleanup() {
    [ -z "${PIDS}" ] || stop_all
    rm -rf "${T}"
}
trap cleanup EXIT
mkdir -p "${T}/m" "${T}/v" "${T}/f" "${T}/tmp" "${T}/home"

# Four HTTP ports whose gRPC companion (port+10000, SeaweedFS's convention)
# is free too. The gRPC ports are left implicit on purpose: in 4.48 the S3
# gateway embedded in `weed filer -s3` dials the filer at port+10000 even when
# -port.grpc says otherwise.
read -r M V F S < <("${PY}" -I -c '
import random, socket
def free(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
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
echo "  ports: master=${M} volume=${V} filer=${F} s3=${S} (gRPC: each +10000)"

AK="CVCPKGTESTACCESSKEY1"
SK="cvcpkg-test-secret-$(date +%s)-$$"
cat > "${T}/s3.json" <<EOF
{"identities": [{"name": "cvcpkg-test",
  "credentials": [{"accessKey": "${AK}", "secretKey": "${SK}"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
EOF

# glog and anything else using os.TempDir() write under TMPDIR; cwd and HOME
# are the temp dir too, so no default "." store lands in the install tree.
cd "${T}"
export TMPDIR="${T}/tmp" HOME="${T}/home"
COMMON="-ip=127.0.0.1 -ip.bind=127.0.0.1"
# shellcheck disable=SC2086
"${WEED}" master ${COMMON} -port="${M}" -mdir="${T}/m" \
    -volumeSizeLimitMB=64 > "${T}/master.log" 2>&1 &
PIDS="${PIDS} master:$!"
# shellcheck disable=SC2086
"${WEED}" volume ${COMMON} -port="${V}" -dir="${T}/v" -max=8 \
    -master="127.0.0.1:${M}" > "${T}/volume.log" 2>&1 &
PIDS="${PIDS} volume:$!"
# shellcheck disable=SC2086
"${WEED}" filer ${COMMON} -port="${F}" -master="127.0.0.1:${M}" \
    -defaultStoreDir="${T}/f" -localSocket="${T}/filer.sock" \
    -s3 -s3.port="${S}" -s3.config="${T}/s3.json" \
    -s3.localSocket="${T}/s3.sock" -s3.port.iceberg=0 -s3.port.lance=0 \
    > "${T}/filer.log" 2>&1 &
PIDS="${PIDS} filer:$!"

if ! "${PY}" -I - "${S}" "${AK}" "${SK}" <<'PY'
import datetime, hashlib, hmac, sys, time, urllib.error, urllib.request

port, ak, sk = int(sys.argv[1]), sys.argv[2], sys.argv[3]
host = "127.0.0.1:%d" % port
region, service = "us-east-1", "s3"

def sign(method, path, body, key_id, secret):
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload, "x-amz-date": amzdate}
    signed = ";".join(sorted(headers))
    uri, _, query = path.partition("?")
    cquery = "&".join(sorted(q if "=" in q else q + "=" for q in query.split("&") if q))
    canonical = "\n".join([method, uri, cquery,
                           "".join("%s:%s\n" % (k, headers[k]) for k in sorted(headers)),
                           signed, payload])
    scope = "%s/%s/%s/aws4_request" % (day, region, service)
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = ("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                                % (key_id, scope, signed, sig))
    return headers

def req(method, path, body=b"", auth=(ak, sk)):
    headers = sign(method, path, body, *auth) if auth else {}
    headers.pop("host", None)
    r = urllib.request.Request("http://%s%s" % (host, path), data=body if method == "PUT" else None,
                               method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        return None, str(e).encode()

# Wait until the gateway answers a signed ListBuckets (filer connected).
deadline = time.time() + 180
while True:
    st, body = req("GET", "/")
    if st == 200:
        break
    if time.time() > deadline:
        sys.exit("FAIL: S3 gateway not ready after 180s (last: %s %r)" % (st, body[:200]))
    time.sleep(1)
print("  OK: S3 gateway up; signed ListBuckets -> 200")

bucket, key = "cvcpkg-test", "dir/hello.txt"
data = b"hello from the cvcpkg seaweedfs test\n" * 100

st, body = req("PUT", "/" + bucket, auth=None)
if st != 403:
    sys.exit("FAIL: unsigned CreateBucket returned %s, expected 403: %r" % (st, body[:300]))
print("  OK: unsigned CreateBucket refused (403)")
st, body = req("PUT", "/" + bucket, auth=(ak, sk + "-wrong"))
if st != 403:
    sys.exit("FAIL: CreateBucket with a wrong secret returned %s, expected 403" % st)
print("  OK: wrong secret refused (403)")

st, body = req("PUT", "/" + bucket)
if st != 200:
    sys.exit("FAIL: signed CreateBucket returned %s: %r" % (st, body[:300]))
print("  OK: signed CreateBucket -> 200")

deadline = time.time() + 120
while True:   # the first write waits for the volume server to register
    st, body = req("PUT", "/%s/%s" % (bucket, key), data)
    if st == 200:
        break
    if time.time() > deadline:
        sys.exit("FAIL: signed PutObject returned %s: %r" % (st, body[:300]))
    time.sleep(1)
print("  OK: signed PutObject (%d bytes) -> 200" % len(data))

st, body = req("GET", "/%s/%s" % (bucket, key), auth=None)
if st != 403:
    sys.exit("FAIL: unsigned GetObject returned %s, expected 403" % st)
print("  OK: unsigned GetObject refused (403)")
st, body = req("GET", "/%s/%s" % (bucket, key))
if st != 200 or body != data:
    sys.exit("FAIL: signed GetObject returned %s (%d bytes, match=%s)" % (st, len(body), body == data))
print("  OK: signed GetObject returned the same %d bytes" % len(body))
st, body = req("GET", "/%s?list-type=2" % bucket)
if st != 200 or key.encode() not in body:
    sys.exit("FAIL: ListObjectsV2 returned %s: %r" % (st, body[:300]))
print("  OK: ListObjectsV2 lists the key")
PY
then
    for l in master volume filer; do echo "--- ${l}.log (tail)"; tail -20 "${T}/${l}.log"; done
    exit 1
fi

stop_all
if [ -n "${KILLED}" ]; then
    echo "  NOTE: SIGTERM did not stop${KILLED} within 20s; SIGKILLed"
else
    echo "  OK: master, volume and filer exited on SIGTERM"
fi
echo "-- seaweedfs smoke test passed --"
