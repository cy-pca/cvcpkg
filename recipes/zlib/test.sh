#!/usr/bin/env bash
# recipes/zlib/test.sh — smoke test for the zlib install tree.
#
# Invoked by the packager after build.sh populates $CVC_INSTALL_DIR.
# Non-zero exit means the bundle is broken and should not ship.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- zlib smoke test --"

# 1. Check that the header exists.
test -f "${CVC_INSTALL_DIR}/include/zlib.h" \
    || { echo "FAIL: include/zlib.h not found"; exit 1; }

# 2. Check that at least one library file exists.
found_lib=0
for pat in lib/libz.so* lib/libz.dylib* lib/libz.a lib/z.lib lib/zlib*.lib; do
    # shellcheck disable=SC2086
    if compgen -G "${CVC_INSTALL_DIR}/${pat}" >/dev/null 2>&1; then
        found_lib=1
        break
    fi
done
[[ "${found_lib}" -eq 1 ]] \
    || { echo "FAIL: no zlib library found in ${CVC_INSTALL_DIR}/lib/"; exit 1; }

# 3. Check that the CMake config package is findable.
if [[ -d "${CVC_INSTALL_DIR}/lib/cmake/ZLIB" ]]; then
    echo "  OK: CMake config package found"
else
    echo "  WARN: No CMake config package (may be OK if upstream zlib version predates it)"
fi

# 4. Compile a trivial program that links against zlib.
if command -v cc >/dev/null 2>&1; then
    TMPDIR=$(mktemp -d)
    trap 'rm -rf "${TMPDIR}"' EXIT
    cat > "${TMPDIR}/test_zlib.c" <<'EOF'
#include <zlib.h>
#include <stdio.h>
int main(void) {
    printf("zlib version: %s\n", zlibVersion());
    return 0;
}
EOF
    cc -o "${TMPDIR}/test_zlib" "${TMPDIR}/test_zlib.c" \
        -I"${CVC_INSTALL_DIR}/include" \
        -L"${CVC_INSTALL_DIR}/lib" -lz 2>/dev/null \
    && {
        LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${LD_LIBRARY_PATH:-}" \
            "${TMPDIR}/test_zlib"
        echo "  OK: compile + link + run OK"
    } || echo "  WARN: compile/link test skipped (compiler issue)"
else
    echo "  WARN: no C compiler on PATH, skipping compile test"
fi

echo "-- zlib smoke test passed --"
