#!/usr/bin/env bash
# recipes/libpq/build.sh — build libpq (PostgreSQL client) on Linux/macOS.
#
# PostgreSQL >= 16 supports Meson. We use it to build only the client
# library (libpq), not the full server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# Meson-based build for libpq only.
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    -Dlibpq=true \
    -Dssl=openssl \
    -Dzlib=enabled \
    -Dreadline=disabled \
    -Dzstd=disabled \
    -Dlz4=disabled \
    -Dnls=disabled

cd "${CVC_BUILD_DIR}"
# Build only the libpq target.
ninja -j "${CVC_JOBS}" src/interfaces/libpq:pq
ninja install

# Generate a minimal CMake find module so find_package(PostgreSQL) works.
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
