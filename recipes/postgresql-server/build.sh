#!/usr/bin/env bash
# recipes/postgresql-server/build.sh — build the PostgreSQL server on
# Linux/macOS/BSD.
#
# PostgreSQL >= 16 uses Meson.  We build the whole tree (the same proven
# configuration as the libpq recipe) and then prune the pure client-only
# programs, leaving the server daemon and server-side tools.  Pruning only
# removes files, so it can never break the binaries that are kept.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Put the cvcpkg prefix bin on PATH so meson finds ninja from our recipe.
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

# ── Prune pure client-only programs ────────────────────────────
# These ship in the postgresql-client package.  The server package
# still keeps pg_isready and pg_config, which are useful on a server
# host, plus the bundled libpq the server-side tools link against.
cd "${CVC_INSTALL_DIR}"
for prog in psql pg_dump pg_dumpall pg_restore pg_amcheck pgbench \
            createdb dropdb createuser dropuser clusterdb reindexdb vacuumdb; do
    rm -f "bin/${prog}"
done

echo "postgresql-server: kept $(ls bin | wc -l | tr -d ' ') programs in bin/"
