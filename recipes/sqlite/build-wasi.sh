#!/usr/bin/env bash
# recipes/sqlite/build-wasi.sh — cross-compile SQLite to wasm32-wasi via wasi-sdk.
#
# SQLite has first-class WASI support in the amalgamation: sqlite3.c does
#     #if defined(__wasi__)
#     # define SQLITE_WASI 1
#     # define SQLITE_OMIT_LOAD_EXTENSION
#     # define SQLITE_THREADSAFE 0
#     #endif
# and the os_unix.c section keyed on SQLITE_WASI drops <sys/mman.h>, the WAL
# shared-memory paths and fchmod/fchown, and defaults the VFS to
# "unix-dotfile" (advisory fcntl locks are declared but not implemented by
# wasi-libc).  So we pass no wasi-specific defines — the target macro does it.
#
# As with build-wasm.sh we bypass ./configure: SQLite 3.49's autosetup
# configure builds a jimsh with $CC and then executes it, which cannot work
# when $CC emits wasm.  The tarball is the amalgamation, so one compile of
# sqlite3.c is the whole library.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${CVC_WASI_SDK_DIR}/share/wasi-sysroot"

# Feature set mirrors what build.sh gets from `./configure --all`.
SQLITE_DEFINES=(
    -DSQLITE_ENABLE_COLUMN_METADATA
    -DSQLITE_ENABLE_FTS4
    -DSQLITE_ENABLE_FTS5
    -DSQLITE_ENABLE_GEOPOLY
    -DSQLITE_ENABLE_RTREE
    -DSQLITE_ENABLE_MATH_FUNCTIONS
    -DSQLITE_ENABLE_PREUPDATE_HOOK
    -DSQLITE_ENABLE_SESSION
)

"${CC}" ${WASI_TARGET_FLAGS} -O2 "${SQLITE_DEFINES[@]}" -c sqlite3.c -o sqlite3.o
"${AR}" crs libsqlite3.a sqlite3.o
"${RANLIB}" libsqlite3.a

mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig" "${CVC_INSTALL_DIR}/include"
install -m 644 libsqlite3.a "${CVC_INSTALL_DIR}/lib/libsqlite3.a"
install -m 644 sqlite3.h sqlite3ext.h "${CVC_INSTALL_DIR}/include/"

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
