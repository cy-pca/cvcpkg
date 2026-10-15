#!/usr/bin/env bash
# recipes/zlib-ng/test.sh — smoke test: compile against the staged libz-ng and
# round-trip a buffer through zng_compress / zng_uncompress.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- zlib-ng smoke test (${CVC_PLATFORM:-native}) --"
test -f "${CVC_INSTALL_DIR}/include/zlib-ng.h" || { echo "FAIL: include/zlib-ng.h missing"; exit 1; }

if ! command -v cc >/dev/null 2>&1; then
    echo "  WARN: no C compiler on PATH, skipping compile test"
    exit 0
fi

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/t.c" <<'EOF'
#include <stdio.h>
#include <string.h>
#include <zlib-ng.h>
int main(void) {
    const char *src = "cvcpkg zlib-ng cvcpkg zlib-ng cvcpkg zlib-ng cvcpkg zlib-ng";
    unsigned char comp[256], out[256];
    size_t clen = sizeof comp, olen = sizeof out;
    if (zng_compress(comp, &clen, (const unsigned char *)src, strlen(src)) != Z_OK) return 1;
    if (zng_uncompress(out, &olen, comp, clen) != Z_OK) return 2;
    if (olen != strlen(src) || memcmp(out, src, olen) != 0) return 3;
    printf("zlib-ng %s round-trip OK (%zu -> %zu bytes)\n", zlibng_version(), strlen(src), clen);
    return 0;
}
EOF
cc -o "${T}/t" "${T}/t.c" -I"${CVC_INSTALL_DIR}/include" -L"${CVC_INSTALL_DIR}/lib" -lz-ng
LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}" \
    "${T}/t"
echo "-- zlib-ng smoke test passed --"
