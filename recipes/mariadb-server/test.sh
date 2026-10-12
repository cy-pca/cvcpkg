#!/usr/bin/env bash
# recipes/mariadb-server/test.sh — smoke-test the MariaDB server bundle.
#
# Initialises a throwaway data directory, starts mariadbd on a free loopback
# port + private socket as the current user, connects over the socket
# (unix_socket auth) and over TCP (password auth), creates a database, user
# and InnoDB/Aria tables, optionally talks to it from Python's pymysql when
# one is importable, and shuts the server down.  Everything runs with
# --no-defaults so a host /etc/my.cnf cannot leak in.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
P="${CVC_INSTALL_DIR}"

echo "-- mariadb-server smoke test (${CVC_PLATFORM:-native}) --"

# On the builder the runtime deps (openssl, pcre2, ...) sit in the deps
# prefix, not next to the staged bundle; once installed they share the
# prefix and the programs find them through their $ORIGIN RPATH alone.
if [[ -n "${CVC_DEPS_PREFIX:-}" && "${CVC_DEPS_PREFIX}" != "${P}" ]]; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
        export DYLD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
    else
        export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    fi
fi

for b in mariadbd mariadb-install-db mariadb mariadb-admin mariadb-dump my_print_defaults; do
    test -x "${P}/bin/${b}" || { echo "FAIL: bin/${b} missing"; exit 1; }
done
for l in mysqld mysql mysql_install_db mysqladmin mysqldump; do
    test -e "${P}/bin/${l}" || { echo "FAIL: compat name bin/${l} missing"; exit 1; }
done
"${P}/bin/mariadbd" --version
"${P}/bin/mariadb" --version

# A short scratch dir: the unix socket path must stay under ~100 bytes.
_base="${TMPDIR:-/tmp}"
[[ ${#_base} -gt 60 ]] && _base=/tmp
T="$(mktemp -d "${_base%/}/mdb.XXXXXX")"
SOCK="${T}/mdb.sock"
PID_FILE="${T}/mariadbd.pid"
SERVER_PID=""

cleanup() {
    local rc=$?
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        "${P}/bin/mariadb-admin" --no-defaults -S "${SOCK}" shutdown 2>/dev/null \
            || kill "${SERVER_PID}" 2>/dev/null || true
        for _ in $(seq 1 30); do kill -0 "${SERVER_PID}" 2>/dev/null || break; sleep 1; done
        kill -9 "${SERVER_PID}" 2>/dev/null || true
    fi
    if [[ ${rc} -ne 0 && -f "${T}/mariadbd.err" ]]; then
        echo "---- mariadbd.err (tail) ----"; tail -n 40 "${T}/mariadbd.err" || true
    fi
    rm -rf "${T}"
    exit "${rc}"
}
trap cleanup EXIT

# A free loopback port (bash /dev/tcp: a refused connect means nobody listens).
PORT=""
for _ in $(seq 1 50); do
    cand=$(( 20000 + (RANDOM % 30000) ))
    if ! (exec 3<>"/dev/tcp/127.0.0.1/${cand}") 2>/dev/null; then PORT="${cand}"; break; fi
done
[[ -n "${PORT}" ]] || { echo "FAIL: no free port found"; exit 1; }

ME="$(id -un)"
USER_ARGS=()
[[ "$(id -u)" -eq 0 ]] && USER_ARGS=(--user=root)

# 1. Initialise the data directory.
"${P}/bin/mariadb-install-db" --no-defaults --basedir="${P}" --datadir="${T}/data" \
    --auth-root-authentication-method=socket --auth-root-socket-user="${ME}" \
    --skip-test-db --skip-name-resolve ${USER_ARGS[@]+"${USER_ARGS[@]}"} > "${T}/install-db.log" 2>&1 \
    || { cat "${T}/install-db.log"; echo "FAIL: mariadb-install-db"; exit 1; }
echo "  OK: mariadb-install-db"

# 2. Start the server.
"${P}/bin/mariadbd" --no-defaults --basedir="${P}" --datadir="${T}/data" \
    --socket="${SOCK}" --port="${PORT}" --bind-address=127.0.0.1 \
    --pid-file="${PID_FILE}" --log-error="${T}/mariadbd.err" \
    --innodb-buffer-pool-size=32M --skip-name-resolve ${USER_ARGS[@]+"${USER_ARGS[@]}"} &
SERVER_PID=$!

up=0
for _ in $(seq 1 90); do
    if "${P}/bin/mariadb-admin" --no-defaults -S "${SOCK}" -u "${ME}" ping >/dev/null 2>&1; then
        up=1; break
    fi
    kill -0 "${SERVER_PID}" 2>/dev/null || break
    sleep 1
done
[[ ${up} -eq 1 ]] || { echo "FAIL: mariadbd did not come up"; exit 1; }
echo "  OK: mariadbd up on 127.0.0.1:${PORT} (pid ${SERVER_PID})"

# 3. Socket connection, unix_socket auth as the OS user.
sq() { "${P}/bin/mariadb" --no-defaults -S "${SOCK}" -u "${ME}" -N -B -e "$1"; }
ver="$(sq 'SELECT VERSION()')"
echo "  SELECT VERSION() -> ${ver}"
case "${ver}" in *MariaDB*) ;; *) echo "FAIL: unexpected version '${ver}'"; exit 1 ;; esac
sq "CREATE DATABASE cvc_smoke;
    CREATE USER 'cvc'@'127.0.0.1' IDENTIFIED BY 'cvc-smoke-pw';
    GRANT ALL PRIVILEGES ON cvc_smoke.* TO 'cvc'@'127.0.0.1';"
engines="$(sq "SELECT GROUP_CONCAT(ENGINE ORDER BY ENGINE) FROM information_schema.ENGINES
               WHERE SUPPORT IN ('YES','DEFAULT')")"
echo "  engines: ${engines}"
case ",${engines}," in *,InnoDB,*) ;; *) echo "FAIL: InnoDB missing"; exit 1 ;; esac
case ",${engines}," in *,Aria,*) ;; *) echo "FAIL: Aria missing"; exit 1 ;; esac

# 4. TCP connection, password auth, InnoDB + Aria tables.
tq() {
    "${P}/bin/mariadb" --no-defaults -h 127.0.0.1 -P "${PORT}" -u cvc -pcvc-smoke-pw \
        --protocol=TCP -N -B cvc_smoke -e "$1"
}
tq "CREATE TABLE t_inno (id INT PRIMARY KEY, s VARCHAR(32)) ENGINE=InnoDB;
    CREATE TABLE t_aria (id INT PRIMARY KEY, s VARCHAR(32)) ENGINE=Aria;
    INSERT INTO t_inno VALUES (1,'one'),(2,'two');
    INSERT INTO t_aria SELECT * FROM t_inno;"
got="$(tq 'SELECT COUNT(*), SUM(id) FROM t_aria')"
[[ "${got}" == $'2\t3' ]] || { echo "FAIL: TCP query returned '${got}'"; exit 1; }
re="$(tq "SELECT 'cvcpkg-11' REGEXP '^[a-z]+-[0-9]+\$'")"
[[ "${re}" == "1" ]] || { echo "FAIL: PCRE2 REGEXP returned '${re}'"; exit 1; }
echo "  OK: TCP + password auth, InnoDB/Aria tables, PCRE2 REGEXP"

tls="$(tq "SHOW SESSION STATUS WHERE Variable_name IN ('Ssl_version','Ssl_cipher')" | tr '\t\n' '= ')"
echo "  TCP session TLS: ${tls}"

"${P}/bin/mariadb-dump" --no-defaults -h 127.0.0.1 -P "${PORT}" -u cvc -pcvc-smoke-pw \
    cvc_smoke t_inno > "${T}/dump.sql" || { echo "FAIL: mariadb-dump"; exit 1; }
grep -q "INSERT INTO \`t_inno\`" "${T}/dump.sql" || { echo "FAIL: mariadb-dump output"; exit 1; }
echo "  OK: mariadb-dump"

# 5. Optional: the pymysql wire protocol (what txwtf-style apps use).
PY=""
# (cvcpkg's python3xx recipes install bin/python3.NN; a host python3 also
# counts if it happens to have pymysql.)
for cand in ${CVC_DEPS_PREFIX:+"${CVC_DEPS_PREFIX}"/bin/python3 "${CVC_DEPS_PREFIX}"/bin/python3.1[0-9]} python3; do
    [[ -n "${cand}" ]] || continue
    if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c 'import pymysql' 2>/dev/null; then
        PY="${cand}"; break
    fi
done
if [[ -n "${PY}" ]]; then
    "${PY}" - "${PORT}" <<'PYEOF'
import sys
import pymysql
conn = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="cvc",
                       password="cvc-smoke-pw", database="cvc_smoke", charset="utf8mb4")
with conn.cursor() as cur:
    cur.execute("SELECT VERSION(), (SELECT COUNT(*) FROM t_inno)")
    ver, n = cur.fetchone()
    assert n == 2, n
    from importlib.metadata import version
    print(f"  OK: PyMySQL {version('PyMySQL')} -> {ver}, rows={n}")
conn.close()
PYEOF
else
    echo "  SKIP: no python3 with pymysql available"
fi

# 6. Clean shutdown.
"${P}/bin/mariadb-admin" --no-defaults -S "${SOCK}" -u "${ME}" shutdown
for _ in $(seq 1 60); do kill -0 "${SERVER_PID}" 2>/dev/null || break; sleep 1; done
if kill -0 "${SERVER_PID}" 2>/dev/null; then echo "FAIL: mariadbd did not stop"; exit 1; fi
wait "${SERVER_PID}" 2>/dev/null || true
SERVER_PID=""
echo "  OK: clean shutdown"

echo "-- mariadb-server smoke test passed --"
