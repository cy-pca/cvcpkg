#!/usr/bin/env bash
# recipes/sqlite/build-cosmo.sh — cross-compile SQLite to a Cosmopolitan APE
# static archive via the cosmocc toolchain.
#
# Cosmopolitan gives SQLite the full POSIX surface os_unix.c wants (fcntl
# advisory locks, mmap, pread/pwrite, pthreads), so the default unix VFS is
# used unchanged.  The one thing an APE cannot do is dlopen(3) — APEs are
# statically linked single files — so run-time extension loading is omitted.
#
# As with the wasm/wasi scripts we bypass ./configure: SQLite 3.49's
# autosetup configure compiles autosetup/jimsh0.c with $CC and then runs it.
# cosmocc output does happen to run on the Linux build host, but driving the
# amalgamation directly keeps all three cross scripts identical in shape and
# avoids libtool emitting a shared-library rule that APE cannot satisfy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cd "${CVC_SOURCE_DIR}"

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
    # APE binaries are statically linked — no dlopen for run-time extensions.
    -DSQLITE_OMIT_LOAD_EXTENSION
    # Cosmopolitan ships pthreads, so keep the serialised threading mode.
    -DSQLITE_THREADSAFE=1
)

"${CC}" -O2 "${SQLITE_DEFINES[@]}" -c sqlite3.c -o sqlite3.o
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
Libs.private: -lm -lpthread
Cflags: -I\${includedir}
PCEOF

cvc_rewrite_install_paths
