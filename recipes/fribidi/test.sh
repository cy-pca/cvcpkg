#!/usr/bin/env bash
# recipes/fribidi/test.sh — the installed bin/fribidi runs on its own run path.
#
# run_test puts the bundle's lib/ on the loader path, which hides a bin/fribidi
# that cannot find lib/libfribidi.so.0 by itself.  cvc.2's could not on FreeBSD
# and NetBSD: meson stripped its $ORIGIN/../lib at install (see
# bin-install-rpath.patch).  So the tool runs here with the loader path cleared,
# and reorders a CapRTL line, which goes through the library.  Running is not
# enough on its own: the host may have a libfribidi.so.0 of its own (Ubuntu
# does), which such a bin/fribidi loads instead, so where there is an ldd it
# must resolve the bundle's.
#
# Not run where bin/fribidi's run path is set later: OpenBSD, whose ld.so has no
# $ORIGIN (cvcpkg's installer bakes an absolute one), and host-delegated Windows
# builds.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

P="${CVC_INSTALL_DIR%/}"
PLAT="${CVC_PLATFORM:-linux}"
echo "-- fribidi test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

test -f "${P}/include/fribidi/fribidi.h" || fail "include/fribidi/fribidi.h missing"
test -f "${P}/lib/pkgconfig/fribidi.pc" || fail "lib/pkgconfig/fribidi.pc missing"
ok "headers and fribidi.pc present"

if [ "${PLAT}" = "openbsd" ]; then
    ok "openbsd: the installer sets bin/fribidi's run path; not run"
    exit 0
fi
if [ -n "${CVC_WINHOST:-}" ]; then
    ok "host-delegated Windows build: bin/fribidi.exe not run"
    exit 0
fi
exe="bin/fribidi"
[ "${PLAT}" = "windows" ] && exe="bin/fribidi.exe"
test -f "${P}/${exe}" || fail "${exe} missing"

out="$(printf 'abc DEF ghi\n' | (
    unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH
    exec "${P}/${exe}" --charset CapRTL --nopad
))" || fail "${exe} did not run with the loader path cleared"
out="${out%$'\r'}"
[ "${out}" = "abc FED ghi" ] || fail "${exe} reordered 'abc DEF ghi' as '${out}', not 'abc FED ghi'"
ok "${exe} runs with the loader path cleared (abc DEF ghi -> ${out})"

lib="${P}/lib/libfribidi.so.0"
case "${PLAT}" in
    linux|freebsd|netbsd) ;;
    *) exit 0 ;;
esac
if [ ! -e "${lib}" ]; then
    ok "static bundle: no lib/libfribidi.so.0 to resolve"
    exit 0
fi
if ! command -v ldd >/dev/null 2>&1; then
    ok "no ldd: which libfribidi ${exe} loads is not checked"
    exit 0
fi
# glibc and FreeBSD: "libfribidi.so.0 => <path> (0x...)"; NetBSD:
# "-lfribidi.0 => <path>".  ldd exits non-zero when a library is missing.
got="$( (unset LD_LIBRARY_PATH; ldd "${P}/${exe}") 2>&1 \
    | sed -n 's/.*fribidi[^ ]* => \([^ ]*\).*/\1/p' | head -n 1)" || true
case "${got}" in
    "") fail "ldd lists no libfribidi for ${exe}" ;;
    not) fail "${exe} cannot find libfribidi.so.0 with the loader path cleared" ;;
esac
# -ef compares the files, so a symlinked prefix (FreeBSD's /home) still matches.
[ "${got}" -ef "${lib}" ] || fail "${exe} loads ${got}, not the bundle's lib/libfribidi.so.0"
ok "${exe} loads the bundle's lib/libfribidi.so.0"
