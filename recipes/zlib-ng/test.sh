#!/usr/bin/env bash
# recipes/zlib-ng/test.sh — smoke test: compile against the staged libz-ng and
# round-trip a buffer through zng_compress / zng_uncompress.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- zlib-ng smoke test (${CVC_PLATFORM:-native}) --"
test -f "${CVC_INSTALL_DIR}/include/zlib-ng.h" || { echo "FAIL: include/zlib-ng.h missing"; exit 1; }

# cc, or gcc where only that exists (cvcpkg's mingw-w64-gcc has no cc.exe).
CC_BIN="$(command -v cc || command -v gcc || true)"
if [[ -z "${CC_BIN}" ]]; then
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
zlib_ng_lib=(-lz-ng)
if [[ "${CVC_PLATFORM:-}" == "windows" ]]; then
    # The MSVC build names its import library lib/zlib-ng.lib (the DLL is
    # bin/zlib-ng2.dll), which -lz-ng never matches: "ld.exe: cannot find
    # -lz-ng" (job 22623).  Link it by file name; the DLL loads from bin/.
    zlib_ng_lib=(-l:zlib-ng.lib)
    export PATH="${CVC_INSTALL_DIR}/bin:${PATH}"
fi
"${CC_BIN}" -o "${T}/t" "${T}/t.c" -I"${CVC_INSTALL_DIR}/include" -L"${CVC_INSTALL_DIR}/lib" "${zlib_ng_lib[@]}"
LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}" \
    "${T}/t"
echo "-- zlib-ng smoke test passed --"
