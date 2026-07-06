#!/usr/bin/env bash
# recipes/sqlite/build.sh — build SQLite from source using autotools amalgamation.
#
# The sqlite-autoconf tarball is the official "amalgamation" distribution:
# a single sqlite3.c file plus a standard ./configure + make build system.
# No extra deps required.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Use gmake on BSDs (GNU make syntax in the generated Makefile).
MAKE=make
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
fi

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    # Enable common extensions used by downstream (JSON, FTS5, R*Tree, math).
    --enable-fts5
    --enable-json1
    --enable-rtree
    --enable-math-functions
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Generate a minimal pkg-config file if the installed one is missing or
# incorrect (some SQLite autoconf releases omit it).
PC="${CVC_INSTALL_DIR}/lib/pkgconfig/sqlite3.pc"
if [[ ! -f "$PC" ]]; then
    mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
    cat > "$PC" <<PCEOF
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: SQLite3
Description: SQL database engine
Version: $(./sqlite3 --version 2>/dev/null | head -1 | cut -d' ' -f1 || echo "3")
Libs: -L\${libdir} -lsqlite3
Libs.private: -lm -lpthread -ldl
Cflags: -I\${includedir}
PCEOF
fi
