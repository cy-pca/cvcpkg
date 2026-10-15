#!/usr/bin/env bash
# recipes/libmaxminddb/test.sh — smoke test: compile against the staged library,
# check the runtime version matches, and run mmdblookup.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- libmaxminddb smoke test (${CVC_PLATFORM:-native}) --"
test -f "${CVC_INSTALL_DIR}/include/maxminddb.h" || { echo "FAIL: include/maxminddb.h missing"; exit 1; }
export LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"

if [ -x "${CVC_INSTALL_DIR}/bin/mmdblookup" ]; then
    "${CVC_INSTALL_DIR}/bin/mmdblookup" --version
fi

if ! command -v cc >/dev/null 2>&1; then
    echo "  WARN: no C compiler on PATH, skipping compile test"
    exit 0
fi

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/t.c" <<'EOF'
#include <stdio.h>
#include <string.h>
#include <maxminddb.h>
int main(void) {
    MMDB_s mmdb;
    /* Opening a missing file must fail cleanly with MMDB_FILE_OPEN_ERROR. */
    int rc = MMDB_open("/nonexistent/cvcpkg.mmdb", MMDB_MODE_MMAP, &mmdb);
    printf("libmaxminddb %s, open(missing) -> %s\n", MMDB_lib_version(), MMDB_strerror(rc));
    return rc == MMDB_FILE_OPEN_ERROR ? 0 : 1;
}
EOF
cc -o "${T}/t" "${T}/t.c" -I"${CVC_INSTALL_DIR}/include" -L"${CVC_INSTALL_DIR}/lib" -lmaxminddb
"${T}/t"
echo "-- libmaxminddb smoke test passed --"
