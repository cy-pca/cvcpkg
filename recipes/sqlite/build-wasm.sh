#!/usr/bin/env bash
# recipes/sqlite/build-wasm.sh — cross-compile SQLite to wasm via Emscripten.
#
# Why not ./configure like build.sh does?  SQLite 3.49's configure is
# autosetup (Tcl): it compiles autosetup/jimsh0.c with $CC and then *runs*
# the result to evaluate the build config.  Under any cross-compiler that
# produces a jimsh the build host cannot execute.  The sqlite-autoconf
# tarball is the amalgamation, so the whole library is one translation
# unit — we compile sqlite3.c straight into a static archive and skip
# configure entirely.  This is the same shape as SQLite's own
# wasm/JS build, which also drives the amalgamation directly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# Feature set mirrors what build.sh gets from `./configure --all`
# (FTS4/5, Geopoly, RTree, Sessions) plus the defaults 3.49 turns on.
SQLITE_DEFINES=(
    -DSQLITE_ENABLE_COLUMN_METADATA
    -DSQLITE_ENABLE_FTS4
    -DSQLITE_ENABLE_FTS5
    -DSQLITE_ENABLE_GEOPOLY
    -DSQLITE_ENABLE_RTREE
    -DSQLITE_ENABLE_MATH_FUNCTIONS
    -DSQLITE_ENABLE_PREUPDATE_HOOK
    -DSQLITE_ENABLE_SESSION
    # Emscripten links statically; there is no dlopen for run-time extensions.
    -DSQLITE_OMIT_LOAD_EXTENSION
    # cvcpkg's emsdk builds are not -pthread, so drop the mutex machinery.
    -DSQLITE_THREADSAFE=0
)

emcc -O2 -fPIC "${SQLITE_DEFINES[@]}" -c sqlite3.c -o sqlite3.o
emar crs libsqlite3.a sqlite3.o
emranlib libsqlite3.a

mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig" "${CVC_INSTALL_DIR}/include"
install -m 644 libsqlite3.a "${CVC_INSTALL_DIR}/lib/libsqlite3.a"
install -m 644 sqlite3.h sqlite3ext.h "${CVC_INSTALL_DIR}/include/"

# The amalgamation ships sqlite3.pc.in but not a generated .pc; emit a
# relocatable one by hand (same shape as build.sh's fallback).
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/sqlite3.pc" <<PCEOF
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: SQLite3
Description: SQL database engine
Version: $(cat VERSION)
Libs: -L\${libdir} -lsqlite3
Libs.private: -lm
Cflags: -I\${includedir}
PCEOF

cvc_rewrite_install_paths
