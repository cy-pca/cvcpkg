#!/usr/bin/env bash
# recipes/postgresql-client/build.sh — build the PostgreSQL client
# programs (psql, pg_dump, …) on Linux/macOS/BSD.
#
# PostgreSQL >= 16 uses Meson.  We build the whole tree (the same proven
# configuration as the libpq recipe) and then prune the server daemon and
# server-side tools, leaving the client programs plus the libpq client
# library they link against.  Pruning only removes files, so it can never
# break the binaries that are kept.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dssl=openssl \
    -Dzlib=enabled \
    -Dreadline=enabled \
    -Dzstd=enabled \
    -Dlz4=enabled \
    -Dnls=enabled \
    -Dgssapi=enabled

cd "${CVC_BUILD_DIR}"
ninja -j "${CVC_JOBS}"
ninja install

# ── Prune the server daemon and server-side tools ──────────────
# These ship in the postgresql-server package.  We keep the client
# programs, the bundled libpq, and the client headers/pkg-config so
# find_package(PostgreSQL) still resolves.
cd "${CVC_INSTALL_DIR}"
for prog in postgres postmaster initdb pg_ctl pg_controldata pg_resetwal \
            pg_rewind pg_basebackup pg_receivewal pg_recvlogical pg_waldump \
            pg_archivecleanup pg_checksums pg_verifybackup pg_upgrade \
            pg_test_fsync pg_test_timing; do
    rm -f "bin/${prog}"
done
# Backend extension modules are server-only.
rm -rf lib/postgresql lib/*/postgresql

echo "postgresql-client: kept $(ls bin | wc -l | tr -d ' ') programs in bin/"

# Generate a minimal CMake find module so find_package(PostgreSQL) works
# against the bundled libpq (mirrors the libpq recipe).
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/PostgreSQL"
cat > "${CVC_INSTALL_DIR}/lib/cmake/PostgreSQL/PostgreSQLConfig.cmake" <<'EOF'
get_filename_component(_PG_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

add_library(PostgreSQL::PostgreSQL UNKNOWN IMPORTED)
find_library(_PG_LIB NAMES pq PATHS "${_PG_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(PostgreSQL::PostgreSQL PROPERTIES
    IMPORTED_LOCATION "${_PG_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${_PG_PREFIX}/include"
)
set(PostgreSQL_FOUND TRUE)
set(PostgreSQL_INCLUDE_DIRS "${_PG_PREFIX}/include")
set(PostgreSQL_LIBRARIES "${_PG_LIB}")
unset(_PG_PREFIX)
unset(_PG_LIB)
EOF
