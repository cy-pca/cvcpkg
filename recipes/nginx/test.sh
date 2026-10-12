#!/usr/bin/env bash
# recipes/nginx/test.sh — smoke-test the nginx bundle.
#
# Runs nginx from a throwaway prefix (-p) with a temp config on a free
# loopback port: `nginx -t`, start, fetch a static file, a PCRE2 regex
# location and (when an openssl CLI is around) the same over TLS, check that
# the shipped sample config parses, then `-s quit`.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
P="${CVC_INSTALL_DIR}"
NGINX="${P}/bin/nginx"

echo "-- nginx smoke test (${CVC_PLATFORM:-native}) --"

# On the builder the runtime deps (openssl, pcre2, zlib) sit in the deps
# prefix, not next to the staged bundle; once installed they share the
# prefix and bin/nginx finds them through its $ORIGIN/../lib RPATH alone.
if [[ -n "${CVC_DEPS_PREFIX:-}" && "${CVC_DEPS_PREFIX}" != "${P}" ]]; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
        export DYLD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
    else
        export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    fi
fi

test -x "${NGINX}" || { echo "FAIL: bin/nginx missing"; exit 1; }
for f in mime.types nginx.conf.sample fastcgi_params; do
    test -f "${P}/etc/nginx/${f}" || { echo "FAIL: etc/nginx/${f} missing"; exit 1; }
done
"${NGINX}" -V 2>&1 | sed 's/^/  /'
for m in http_ssl_module http_v2_module stream pcre-jit http_realip_module; do
    "${NGINX}" -V 2>&1 | grep -q -- "--with-${m}" || { echo "FAIL: built without ${m}"; exit 1; }
done

T="$(mktemp -d "${TMPDIR:-/tmp}/ngx.XXXXXX")"
cleanup() {
    local rc=$?
    if [[ -f "${T}/logs/nginx.pid" ]]; then
        "${NGINX}" -p "${T}" -c nginx.conf -s stop 2>/dev/null \
            || kill "$(cat "${T}/logs/nginx.pid")" 2>/dev/null || true
    fi
    if [[ ${rc} -ne 0 && -f "${T}/logs/error.log" ]]; then
        echo "---- error.log (tail) ----"; tail -n 30 "${T}/logs/error.log" || true
    fi
    rm -rf "${T}"
    exit "${rc}"
}
trap cleanup EXIT
mkdir -p "${T}/logs" "${T}/html"
echo "hello from cvcpkg nginx" > "${T}/html/index.html"

free_port() {
    local cand
    for _ in $(seq 1 50); do
        cand=$(( 20000 + (RANDOM % 30000) ))
        if ! (exec 3<>"/dev/tcp/127.0.0.1/${cand}") 2>/dev/null; then echo "${cand}"; return 0; fi
    done
    return 1
}
PORT="$(free_port)" || { echo "FAIL: no free port"; exit 1; }

# Optional TLS listener when an openssl CLI can mint a throwaway cert.
TLS_SERVER=""
TLS_PORT=""
OPENSSL_BIN=""
for cand in "${CVC_DEPS_PREFIX:+${CVC_DEPS_PREFIX}/bin/openssl}" openssl; do
    [[ -n "${cand}" ]] && command -v "${cand}" >/dev/null 2>&1 && { OPENSSL_BIN="${cand}"; break; }
done
if [[ -n "${OPENSSL_BIN}" ]] && command -v curl >/dev/null 2>&1 \
   && "${OPENSSL_BIN}" req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
        -keyout "${T}/cert.key" -out "${T}/cert.pem" >/dev/null 2>&1; then
    TLS_PORT="$(free_port)" || TLS_PORT=""
    while [[ -n "${TLS_PORT}" && "${TLS_PORT}" == "${PORT}" ]]; do TLS_PORT="$(free_port)"; done
    [[ -n "${TLS_PORT}" ]] && TLS_SERVER="
    server {
        listen 127.0.0.1:${TLS_PORT} ssl;
        http2 on;
        ssl_certificate     cert.pem;
        ssl_certificate_key cert.key;
        location / { root html; }
    }"
fi

cat > "${T}/nginx.conf" <<EOF
worker_processes 1;
error_log logs/error.log info;
pid logs/nginx.pid;
pcre_jit on;
events { worker_connections 64; }
http {
    include ${P}/etc/nginx/mime.types;
    access_log logs/access.log;
    server {
        listen 127.0.0.1:${PORT};
        location / { root html; }
        location ~ ^/re/(?<num>[0-9]+)\$ { return 200 "re:\$num\n"; }
        location = /nginx_status { stub_status; }
    }${TLS_SERVER}
}
EOF

"${NGINX}" -p "${T}" -c nginx.conf -t
"${NGINX}" -p "${T}" -c nginx.conf
for _ in $(seq 1 50); do [[ -s "${T}/logs/nginx.pid" ]] && break; sleep 0.2; done
[[ -s "${T}/logs/nginx.pid" ]] || { echo "FAIL: nginx did not start"; exit 1; }
echo "  OK: nginx started on 127.0.0.1:${PORT} (pid $(cat "${T}/logs/nginx.pid"))"

http_get() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 10 "http://127.0.0.1:${PORT}$1"
    else
        exec 3<>"/dev/tcp/127.0.0.1/${PORT}"
        printf 'GET %s HTTP/1.0\r\nHost: localhost\r\n\r\n' "$1" >&3
        sed '1,/^\r$/d' <&3
        exec 3<&-
    fi
}
body="$(http_get /)"
[[ "${body}" == "hello from cvcpkg nginx" ]] || { echo "FAIL: GET / returned '${body}'"; exit 1; }
echo "  OK: GET / served the static file"
body="$(http_get /re/42)"
[[ "${body}" == "re:42" ]] || { echo "FAIL: regex location returned '${body}'"; exit 1; }
echo "  OK: PCRE2 regex location"
http_get /nginx_status | grep -q "Active connections" || { echo "FAIL: stub_status"; exit 1; }
echo "  OK: stub_status"
if [[ -n "${TLS_SERVER}" ]]; then
    body="$(curl -fsS -k --max-time 10 "https://127.0.0.1:${TLS_PORT}/")"
    [[ "${body}" == "hello from cvcpkg nginx" ]] || { echo "FAIL: HTTPS GET returned '${body}'"; exit 1; }
    echo "  OK: HTTPS (OpenSSL) on 127.0.0.1:${TLS_PORT}"
else
    echo "  SKIP: TLS check (no openssl CLI or curl)"
fi

"${NGINX}" -p "${T}" -c nginx.conf -s quit
for _ in $(seq 1 50); do [[ -f "${T}/logs/nginx.pid" ]] || break; sleep 0.2; done
[[ ! -f "${T}/logs/nginx.pid" ]] || { echo "FAIL: nginx did not quit"; exit 1; }
echo "  OK: nginx -s quit"

# The shipped sample must parse once copied next to mime.types.
S="${T}/sample"
mkdir -p "${S}/logs"
cp "${P}/etc/nginx/nginx.conf.sample" "${S}/nginx.conf"
cp "${P}/etc/nginx/mime.types" "${S}/"
"${NGINX}" -p "${S}" -c nginx.conf -t
echo "  OK: nginx.conf.sample passes nginx -t"

echo "-- nginx smoke test passed --"
