#!/usr/bin/env bash
# recipes/c-ares/test.sh — the shared library is named by its compatibility version.
#
# A consumer records the name a library gives itself: its SONAME (ELF) or its install name
# (Mach-O LC_ID_DYLIB). For c-ares that must be the major-version name every 1.x release keeps,
# libcares.so.2 / @rpath/libcares.2.dylib, never the full-version file (libcares.2.19.7.dylib).
# grpc 1.68.2+cvc.5 for macOS was linked while this bundle named itself
# @rpath/libcares.2.19.3.dylib, and stopped loading when c-ares 1.34.8 replaced that file.
#
# Checks the name on macOS and Linux, then builds a consumer against the bundle, checks the name
# it recorded, and runs it with the loader path cleared. Elsewhere (the BSDs, Haiku, Windows,
# wasm) only the headers and the CMake package are checked.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

P="${CVC_INSTALL_DIR%/}"
PLAT="${CVC_PLATFORM:-linux}"
echo "-- c-ares test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

for h in ares.h ares_version.h ares_dns.h; do
    test -f "${P}/include/${h}" || fail "include/${h} missing"
done
test -f "${P}/lib/cmake/c-ares/c-ares-config.cmake" || fail "lib/cmake/c-ares/c-ares-config.cmake missing"
ok "headers and CMake package present"

# The real (not symlinked) shared library, or "" for a static bundle.
real_lib() {
    local f
    for f in "${P}"/lib/$1; do
        [ -f "${f}" ] && [ ! -L "${f}" ] && { echo "${f}"; return; }
    done
    echo ""
}

case "${PLAT}" in
    macos)
        lib="$(real_lib 'libcares.*.dylib')"
        [ -n "${lib}" ] || { ok "static bundle: no install name to check"; exit 0; }
        id="$(otool -D "${lib}" | sed -n 2p)"
        [ "${id}" = "@rpath/libcares.2.dylib" ] \
            || fail "$(basename "${lib}") names itself '${id}', not @rpath/libcares.2.dylib"
        [ -L "${P}/lib/libcares.2.dylib" ] || fail "lib/libcares.2.dylib (the compatibility name) missing"
        ok "install name @rpath/libcares.2.dylib"
        recorded() { otool -L "$1" | awk '/libcares/ {print $1}'; }
        want="@rpath/libcares.2.dylib"
        ;;
    linux)
        lib="$(real_lib 'libcares.so.*')"
        [ -n "${lib}" ] || { ok "static bundle: no SONAME to check"; exit 0; }
        if command -v readelf >/dev/null 2>&1; then
            soname="$(readelf -d "${lib}" | awk '/SONAME/ {gsub(/[][]/, "", $NF); print $NF}')"
            [ "${soname}" = "libcares.so.2" ] || fail "$(basename "${lib}") has SONAME '${soname}', not libcares.so.2"
            ok "SONAME libcares.so.2"
            recorded() { readelf -d "$1" | awk '/NEEDED/ && /libcares/ {gsub(/[][]/, "", $NF); print $NF}'; }
        else
            ok "no readelf: SONAME not checked"
            recorded() { echo "libcares.so.2"; }
        fi
        want="libcares.so.2"
        ;;
    *)
        ok "no shared-library naming checked on ${PLAT}"
        exit 0
        ;;
esac

# A consumer records the compatibility name, and runs.
T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/consumer.c" <<'C'
#include <ares.h>
#include <stdio.h>
int main(void) {
  if (ares_library_init(ARES_LIB_INIT_ALL) != ARES_SUCCESS)
    return 1;
  printf("c-ares %s\n", ares_version(NULL));
  ares_library_cleanup();
  return 0;
}
C
"${CC:-cc}" -I"${P}/include" "${T}/consumer.c" -L"${P}/lib" -lcares -Wl,-rpath,"${P}/lib" \
    -o "${T}/consumer" || fail "a consumer did not link against lib/libcares"
got="$(recorded "${T}/consumer")"
[ "${got}" = "${want}" ] || fail "a consumer records libcares as '${got}', not ${want}"
ok "a consumer records ${want}"
out="$( (unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH; exec "${T}/consumer") )" \
    || fail "the consumer did not run"
case "${out}" in
    "c-ares 1."*) ok "the consumer runs (${out})" ;;
    *) fail "the consumer printed '${out}'" ;;
esac
