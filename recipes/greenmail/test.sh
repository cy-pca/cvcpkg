#!/usr/bin/env bash
# recipes/greenmail/test.sh — start GreenMail, send + fetch a message, stop it.
#
# The launcher runs from a scratch prefix laid out like a real install (the
# staged greenmail files plus the temurin-jre21 runtime dependency under
# lib/jvm/), with GREENMAIL_JAVA / JAVA_HOME unset and no other java on PATH,
# so it has to find the cvcpkg JRE beside it. SMTP, IMAP and the REST API are
# bound to 127.0.0.1 on free ports -- never the fixed 3025/3143 defaults, which
# other jobs on a shared builder may hold. Python's stdlib does the mail work.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- greenmail smoke test (${CVC_PLATFORM:-native}) --"

PY="$(command -v python3 || command -v python || true)"
[ -n "${PY}" ] || { echo "FAIL: python3 is required for the SMTP/IMAP round trip"; exit 1; }

# The JRE comes from the runtime dependency (deps prefix) -- or this prefix,
# if both were staged together.
JH=""
for p in "${CVC_INSTALL_DIR}" "${CVC_DEPS_PREFIX:-}"; do
    [ -n "${p}" ] || continue
    if [ -x "${p}/lib/jvm/temurin-jre21/bin/java" ] || [ -x "${p}/lib/jvm/temurin-jre21/bin/java.exe" ]; then
        JH="${p}/lib/jvm/temurin-jre21"; break
    fi
done
[ -n "${JH}" ] || { echo "FAIL: temurin-jre21 not found in the install or deps prefix"; exit 1; }

T="$(mktemp -d)"
GM_PID=""
cleanup() {
    if [ -n "${GM_PID}" ]; then kill "${GM_PID}" 2>/dev/null || true; wait "${GM_PID}" 2>/dev/null || true; fi
    rm -rf "${T}"
}
trap cleanup EXIT

# A prefix shaped like `cvcpkg install greenmail`: our files + the JRE.
P="${T}/prefix"
mkdir -p "${P}/bin" "${P}/share" "${P}/lib/jvm"
cp -p "${CVC_INSTALL_DIR}/bin/greenmail" "${P}/bin/"
cp -pR "${CVC_INSTALL_DIR}/share/greenmail" "${P}/share/"
ln -s "${JH}" "${P}/lib/jvm/temurin-jre21"

read -r SMTP IMAP API < <("${PY}" -I -c '
import socket
socks = [socket.socket() for _ in range(3)]
for s in socks: s.bind(("127.0.0.1", 0))
print(*[s.getsockname()[1] for s in socks])
for s in socks: s.close()
')
echo "  ports: smtp=${SMTP} imap=${IMAP} api=${API}"

GM_OPTS="-Dgreenmail.smtp.hostname=127.0.0.1 -Dgreenmail.smtp.port=${SMTP}"
GM_OPTS="${GM_OPTS} -Dgreenmail.imap.hostname=127.0.0.1 -Dgreenmail.imap.port=${IMAP}"
GM_OPTS="${GM_OPTS} -Dgreenmail.api.hostname=127.0.0.1 -Dgreenmail.api.port=${API}"
GM_OPTS="${GM_OPTS} -Dgreenmail.auth.disabled"
env -u GREENMAIL_JAVA -u JAVA_HOME -u JAVA_OPTS PATH=/usr/bin:/bin \
    GREENMAIL_OPTS="${GM_OPTS}" "${P}/bin/greenmail" > "${T}/greenmail.log" 2>&1 &
GM_PID=$!

"${PY}" -I - "${SMTP}" "${IMAP}" "${API}" "${GM_PID}" "${T}/greenmail.log" <<'PY'
import email, imaplib, json, os, smtplib, socket, sys, time, urllib.request, uuid
from email.message import EmailMessage

smtp_port, imap_port, api_port, pid, log = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]

def fail(msg):
    print("FAIL: " + msg)
    print(open(log, errors="replace").read()[-4000:])
    sys.exit(1)

deadline = time.time() + 120
for port in (smtp_port, imap_port, api_port):
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
            break
        except OSError:
            try:
                os.kill(pid, 0)
            except OSError:
                fail("greenmail exited before opening port %d" % port)
            if time.time() > deadline:
                fail("port %d not open after 120s" % port)
            time.sleep(0.5)
print("  OK: greenmail listening (SMTP, IMAP, API)")

with urllib.request.urlopen("http://127.0.0.1:%d/api/service/readiness" % api_port, timeout=10) as r:
    if r.status != 200:
        fail("REST readiness returned %d" % r.status)
print("  OK: REST API /api/service/readiness -> 200")

token = uuid.uuid4().hex
rcpt = "qa-inbox@cvcpkg.test"
msg = EmailMessage()
msg["From"] = "sender@cvcpkg.test"
msg["To"] = rcpt
msg["Subject"] = "cvcpkg greenmail " + token
msg.set_content("hello from the cvcpkg greenmail test " + token)
with smtplib.SMTP("127.0.0.1", smtp_port, timeout=30) as s:
    s.send_message(msg)
print("  OK: SMTP accepted the message")

for attempt in range(40):
    with imaplib.IMAP4("127.0.0.1", imap_port, timeout=30) as m:
        # auth.disabled: any password is accepted for the recipient's login.
        m.login(rcpt, "anything")
        m.select("INBOX")
        typ, data = m.search(None, "SUBJECT", token)
        ids = data[0].split()
        if ids:
            typ, parts = m.fetch(ids[0], "(RFC822)")
            got = email.message_from_bytes(parts[0][1])
            body = got.get_payload(decode=True).decode()
            if token not in body:
                fail("fetched message body does not carry the token")
            print("  OK: IMAP fetched it back (%s)" % got["Subject"])
            break
    time.sleep(0.25)
else:
    fail("message never showed up in the IMAP inbox")
PY

kill "${GM_PID}"
for _ in $(seq 1 60); do kill -0 "${GM_PID}" 2>/dev/null || break; sleep 0.5; done
if kill -0 "${GM_PID}" 2>/dev/null; then echo "FAIL: greenmail did not stop on SIGTERM"; exit 1; fi
wait "${GM_PID}" 2>/dev/null || true
GM_PID=""
grep -q "temurin-jre21/bin/java" "${T}/greenmail.log" \
    || { echo "FAIL: launcher did not pick the prefix JRE"; head -3 "${T}/greenmail.log"; exit 1; }
echo "  OK: launcher used the prefix's temurin-jre21; stopped cleanly on SIGTERM"

echo "-- greenmail smoke test passed --"
