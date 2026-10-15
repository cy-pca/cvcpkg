#!/usr/bin/env bash
# recipes/mariadb-server/build.sh — build the MariaDB server (Linux/macOS/BSD).
#
# A deliberately small server: InnoDB + Aria (plus the always-on MyISAM, CSV,
# MEMORY, MRG_MyISAM, SEQUENCE and PERFORMANCE_SCHEMA) and the cheap
# self-contained engines/plugins (Archive, Blackhole, FederatedX, auth_ed25519,
# unix_socket, server_audit, ...).  Everything that drags in a large tree or an
# extra library — ColumnStore, RocksDB, Mroonga, Spider, CONNECT, OQGraph,
# Sphinx, S3, DuckDB, Galera/wsrep, PAM, GSSAPI, AWS/Hashicorp key management,
# the bzip2/lz4/lzma/lzo/snappy InnoDB compression providers, cracklib — is
# switched off, as are the embedded server, the test suite and unit tests.
#
# Every optional library is either a cvcpkg dependency (openssl, zlib, pcre2,
# zstd, ncurses) or explicitly disabled, so nothing is silently picked up from
# the build host (libaio, liburing, libsystemd, libnuma, host readline,
# lz4/lzma/lzo/snappy/bzip2 for the InnoDB providers, curl for S3, ...).
#
# The client library is NOT shipped: the server tree builds its bundled
# Connector/C (libmariadb/) to link the client programs statically, then this
# script deletes the shared libmariadb, its headers, pkg-config file and
# mariadb_config.  Applications link the separate mariadb-connector-c package,
# which can sit in the same prefix without a file clash.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Build tools (cmake, ninja, bison, pkg-config) come from the build prefix;
# libraries from the deps prefix.  comp_err and friends run during the build
# and need the deps' shared libraries before RPATHs are final.
for _p in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_p}" && -d "${_p}/bin" ]] && export PATH="${_p}/bin:${PATH}"
done
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    export DYLD_LIBRARY_PATH="${DEPS}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${DEPS}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

# cvcpkg's bison bakes its own build-time prefix in as the location of its
# m4 skeletons and of m4; point it at the relocated copies (as mesa and
# gstreamer do), never at the host's.
for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    if [[ -d "${_root}/share/bison" && -z "${BISON_PKGDATADIR:-}" ]]; then
        export BISON_PKGDATADIR="${_root}/share/bison"
    fi
    if [[ -x "${_root}/bin/m4" && -z "${M4:-}" ]]; then
        export M4="${_root}/bin/m4"
    fi
done
unset _root

# ── {fmt} (MIT), header-only ───────────────────────────────────
# MariaDB >= 10.11 needs {fmt}; with WITH_LIBFMT=bundled its cmake downloads
# this exact zip mid-build (pinned only by MD5, cmake/libfmt.cmake).  Fetch it
# here instead, pinned by sha256, and point the build at it as a "system" fmt.
FMT_VER="12.2.0"
FMT_SHA256="a2f4a8d51178f954e4c339007f77edd76ba0cb2e36f87a48e5a5403d9be5878f"
FMT_ZIP="${CVC_BUILD_DIR}/fmt-${FMT_VER}.zip"
mkdir -p "${CVC_BUILD_DIR}"
if [[ ! -f "${FMT_ZIP}" ]]; then
    curl -fsSL --retry 3 -o "${FMT_ZIP}.part" \
        "https://github.com/fmtlib/fmt/releases/download/${FMT_VER}/fmt-${FMT_VER}.zip"
    mv "${FMT_ZIP}.part" "${FMT_ZIP}"
fi
if command -v sha256sum >/dev/null 2>&1; then
    echo "${FMT_SHA256}  ${FMT_ZIP}" | sha256sum -c -
else
    echo "${FMT_SHA256}  ${FMT_ZIP}" | shasum -a 256 -c -
fi
rm -rf "${CVC_BUILD_DIR}/fmt-${FMT_VER}"
(cd "${CVC_BUILD_DIR}" && cmake -E tar xf "${FMT_ZIP}")
FMT_INCLUDE="${CVC_BUILD_DIR}/fmt-${FMT_VER}/include"
test -f "${FMT_INCLUDE}/fmt/args.h"

# ── Drop the source of engines that are never built ────────────
# cmake adds every storage/<engine> directory it finds; these are also
# switched off below, but deleting them first keeps cmake from even
# configuring them and saves ~1 GB of scratch.  (storage/rocksdb's range-lock
# library is AGPL-3.0, one more reason it must stay out.)
for d in columnstore rocksdb mroonga duckdb spider connect oqgraph sphinx; do
    rm -rf "${CVC_SOURCE_DIR}/storage/${d}"
done

# ── Plugins switched off ───────────────────────────────────────
OFF_PLUGINS=(
    COLUMNSTORE CONNECT DUCKDB MROONGA OQGRAPH ROCKSDB SPHINX SPIDER S3
    AUTH_PAM AUTH_PAM_V1 AUTH_GSSAPI
    AWS_KEY_MANAGEMENT HASHICORP_KEY_MANAGEMENT
    PROVIDER_BZIP2 PROVIDER_LZ4 PROVIDER_LZMA PROVIDER_LZO PROVIDER_SNAPPY
    CRACKLIB_PASSWORD_CHECK
)
PLUGIN_ARGS=()
for p in "${OFF_PLUGINS[@]}"; do PLUGIN_ARGS+=("-DPLUGIN_${p}=NO"); done

# Loader search path: bin/ -> ../lib; plugins live in lib/mysql/plugin/.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    RPATH_ARGS=(-DCMAKE_INSTALL_RPATH="@loader_path/../lib;@loader_path/../../../lib")
else
    RPATH_ARGS=(-DCMAKE_INSTALL_RPATH="\$ORIGIN/../lib;\$ORIGIN/../../../lib")
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_PREFIX_PATH="${DEPS}${CVC_BUILD_PREFIX:+;${CVC_BUILD_PREFIX}}" \
    "${RPATH_ARGS[@]}" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=OFF \
    -DINSTALL_LAYOUT=STANDALONE \
    -DINSTALL_BINDIR=bin \
    -DINSTALL_SBINDIR=bin \
    -DINSTALL_SCRIPTDIR=bin \
    -DINSTALL_LIBDIR=lib \
    -DINSTALL_PLUGINDIR=lib/mysql/plugin \
    -DINSTALL_INCLUDEDIR=include/mysql \
    -DINSTALL_SHAREDIR=share \
    -DINSTALL_MYSQLSHAREDIR=share/mariadb \
    -DINSTALL_SUPPORTFILESDIR=share/mariadb/support-files \
    -DINSTALL_DOCDIR=share/doc/mariadb-server \
    -DINSTALL_DOCREADMEDIR=share/doc/mariadb-server \
    -DINSTALL_MANDIR=share/man \
    -DINSTALL_INFODIR=share/info \
    -DINSTALL_MYSQLTESTDIR= \
    -DINSTALL_SQLBENCHDIR= \
    -DINSTALL_UNIX_ADDRDIR=/tmp/mysql.sock \
    -DWITH_SSL=system \
    -DOPENSSL_ROOT_DIR="${DEPS}" \
    -DWITH_ZLIB=system \
    -DWITH_PCRE=system \
    -DWITH_LIBFMT=system \
    -DLIBFMT_INCLUDE_DIR="${FMT_INCLUDE}" \
    -DWITH_READLINE=ON \
    -DCURSES_NEED_NCURSES=ON \
    -DWITH_EMBEDDED_SERVER=OFF \
    -DWITH_UNIT_TESTS=OFF \
    -DWITH_WSREP=OFF \
    -DWITH_SYSTEMD=no \
    -DWITH_NUMA=OFF \
    -DWITH_URING=OFF \
    -DWITH_LIBAIO=OFF \
    -DCMAKE_DISABLE_FIND_PACKAGE_URING=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_LIBAIO=ON \
    -DWITH_SAFEMALLOC=OFF \
    ${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"}

cmake --build "${CVC_BUILD_DIR}/build" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/build"

cd "${CVC_INSTALL_DIR}"

# ── Drop the client library + development files ────────────────
# libmariadb (LGPL Connector/C) is the mariadb-connector-c package's job; the
# client programs here link it statically.  Server plugin headers are dropped
# too: this package is a server, not an SDK.
rm -f lib/libmariadb.* lib/libmariadbclient.* lib/libmysqlclient* \
      lib/libmysqlservices.a lib/libmariadbd.* lib/libmysqld.*
rm -rf include lib/pkgconfig/libmariadb.pc lib/pkgconfig/mariadb.pc
rmdir lib/pkgconfig 2>/dev/null || true
rm -f bin/mariadb_config bin/mysql_config bin/mariadb-config \
      share/man/man1/mariadb_config.1 share/man/man1/mysql_config.1 \
      share/man/man1/mariadb-config.1
# Test-only programs (the test suite itself is not installed).
rm -f bin/mariadb-test bin/mysqltest bin/mariadb-client-test bin/mysql_client_test \
      bin/mariadb-test-embedded bin/mysqltest_embedded \
      share/man/man1/mariadb-test*.1 share/man/man1/mysqltest*.1 \
      share/man/man1/mariadb-client-test*.1 share/man/man1/mysql_client_test*.1
# The Connector/C API man pages (man3) document the client library, which is
# not shipped; the man1 pages below are for programs that are not built here.
rm -rf share/man/man3
rm -f share/man/man1/mariadb-embedded.1 share/man/man1/mysql_embedded.1 \
      share/man/man1/mysql-test-run.pl.1 share/man/man1/mysql-stress-test.pl.1 \
      share/man/man1/my_safe_process.1 share/man/man1/mariadb-service-convert.1
# Example and test-suite plugins (upstream installs them for mysql-test).
for p in adt_null auth_0x0100 auth_test_plugin debug_key_management dialog_examples \
         example_key_management func_test ha_example ha_test_sql_discovery \
         libdaemon_example mypluglib qa_auth_client qa_auth_interface qa_auth_server \
         test_sql_service test_versioning type_test; do
    rm -f "lib/mysql/plugin/${p}.so"
done
rm -f lib/mysql/plugin/daemon_example.ini share/doc/mariadb-server/hashicorp_key_management.txt
# aclocal macro for the client lib.
rm -rf share/aclocal
# Nothing may land in the prefix root (INSTALL_DOCREADMEDIR points the
# top-level docs at share/doc/mariadb-server; this catches stragglers).
for f in COPYING CREDITS EXCEPTIONS-CLIENT INSTALL-BINARY README.md README-wsrep \
         THIRDPARTY; do
    if [[ -f "${f}" ]]; then
        mkdir -p share/doc/mariadb-server
        mv -f "${f}" share/doc/mariadb-server/
    fi
done

# ── Licence texts ──────────────────────────────────────────────
L=share/licenses/mariadb-server
mkdir -p "${L}"
install -m 0644 "${CVC_SOURCE_DIR}/COPYING"              "${L}/COPYING"
install -m 0644 "${CVC_SOURCE_DIR}/THIRDPARTY"           "${L}/THIRDPARTY"
install -m 0644 "${CVC_SOURCE_DIR}/libmariadb/COPYING.LIB" "${L}/libmariadb-COPYING.LIB"
install -m 0644 "${CVC_SOURCE_DIR}/extra/readline/COPYING" "${L}/readline-COPYING"
install -m 0644 "${CVC_SOURCE_DIR}/storage/innobase/COPYING.Google"  "${L}/innobase-COPYING.Google"
install -m 0644 "${CVC_SOURCE_DIR}/storage/innobase/COPYING.Percona" "${L}/innobase-COPYING.Percona"
install -m 0644 "${CVC_BUILD_DIR}/fmt-${FMT_VER}/LICENSE" "${L}/fmt-LICENSE"

# ── Sample configuration ───────────────────────────────────────
mkdir -p etc/mariadb
install -m 0644 "${SCRIPT_DIR}/my.cnf.sample" etc/mariadb/my.cnf.sample

echo "mariadb-server: $(ls bin | wc -l | tr -d ' ') entries in bin/, \
$(ls lib/mysql/plugin | wc -l | tr -d ' ') in lib/mysql/plugin/"
