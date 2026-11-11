#!/usr/bin/env bash
# recipes/wasmtime/test.sh: consumers link libwasmtime by name, and run once installed.
#
# Upstream's Linux libwasmtime.so has no SONAME, and cvc.1's wasmtime::wasmtime was an UNKNOWN
# imported target. So CMake linked the library by its path, and every consumer recorded that path
# as DT_NEEDED (../wt/lib/libwasmtime.so in transfix/libcvc#1148): it loaded from nowhere else once
# installed. build.sh now sets the SONAME, and the generated target is SHARED with IMPORTED_SONAME
# in a shared bundle, or STATIC over libwasmtime.a in a static one.
#
# On Linux and macOS this checks the library's own name, builds smoke/ (a find_package(wasmtime)
# consumer that runs a guest) and checks what it records. Then it copies the consumer into a
# bin/ + lib/ layout, whose only run path is ../lib, and runs it there with the loader path cleared.
# Elsewhere (Windows) only the headers and the CMake package are checked.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

P="${CVC_INSTALL_DIR%/}"
PLAT="${CVC_PLATFORM:-linux}"
SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/smoke" && pwd)"
echo "-- wasmtime test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

for h in wasmtime.h wasm.h wasi.h; do
    test -f "${P}/include/${h}" || fail "include/${h} missing"
done
CONFIG="${P}/lib/cmake/wasmtime/wasmtimeConfig.cmake"
test -f "${CONFIG}" || fail "lib/cmake/wasmtime/wasmtimeConfig.cmake missing"
ok "headers and CMake package present"

case "${PLAT}" in
    linux)
        lib="${P}/lib/libwasmtime.so"
        want="libwasmtime.so"
        if command -v patchelf >/dev/null 2>&1; then
            own_name() { patchelf --print-soname "$1"; }
            needed() { patchelf --print-needed "$1"; }
        else
            own_name() { readelf -d "$1" | awk '/SONAME/ {gsub(/[][]/, "", $NF); print $NF}'; }
            needed() { readelf -d "$1" | awk '/NEEDED/ {gsub(/[][]/, "", $NF); print $NF}'; }
        fi
        [ -f "${lib}" ] || fail "lib/libwasmtime.so missing"
        got="$(own_name "${lib}")"
        [ "${got}" = "${want}" ] || fail "libwasmtime.so has SONAME '${got}', not ${want}"
        ok "SONAME ${want}"
        ;;
    macos)
        lib="${P}/lib/libwasmtime.dylib"
        want="@rpath/libwasmtime.dylib"
        needed() { otool -L "$1" | awk 'NR > 1 {print $1}'; }
        [ -f "${lib}" ] || fail "lib/libwasmtime.dylib missing"
        got="$(otool -D "${lib}" | sed -n 2p)"
        [ "${got}" = "${want}" ] || fail "libwasmtime.dylib names itself '${got}', not ${want}"
        ok "install name ${want}"
        ;;
    *)
        ok "no consumer built on ${PLAT}"
        exit 0
        ;;
esac

# The bundle's link variant, as build.sh wrote it into the config, picks the target's type.
variant="$(sed -n 's/^set(_WASMTIME_LINK "\(.*\)")$/\1/p' "${CONFIG}")"
case "${variant}" in
    shared) want_type=SHARED_LIBRARY ;;
    static) want_type=STATIC_LIBRARY ;;
    *) fail "wasmtimeConfig.cmake names no link variant ('${variant}')" ;;
esac

cmake="$(command -v cmake)" || fail "cmake (a build dependency) is not on PATH"
ninja="$(command -v ninja)" || fail "ninja (a build dependency) is not on PATH"
echo "  cmake: ${cmake}; ninja: ${ninja}"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
"${cmake}" -G Ninja -DCMAKE_MAKE_PROGRAM="${ninja}" -S "${SMOKE_DIR}" -B "${T}/b" \
    -DCMAKE_BUILD_TYPE=Release -Dwasmtime_DIR="${P}/lib/cmake/wasmtime" >"${T}/configure.log" 2>&1 \
    || { cat "${T}/configure.log"; fail "the find_package(wasmtime) consumer did not configure"; }
"${cmake}" --build "${T}/b" >"${T}/build.log" 2>&1 \
    || { cat "${T}/build.log"; fail "the find_package(wasmtime) consumer did not build"; }

type="$(cat "${T}/b/wasmtime_target_type.txt")"
[ "${type}" = "${want_type}" ] || fail "wasmtime::wasmtime is ${type} in a ${variant} bundle"
exe="${T}/b/wasmtime_smoke"
recorded="$(needed "${exe}" | grep wasmtime || true)"
if [ "${type}" = SHARED_LIBRARY ]; then
    [ "${recorded}" = "${want}" ] || fail "the consumer records libwasmtime as '${recorded}', not ${want}"
    ok "wasmtime::wasmtime is SHARED; the consumer records ${want}"
else
    [ -z "${recorded}" ] || fail "the static library's consumer still needs '${recorded}'"
    ok "wasmtime::wasmtime is STATIC (libwasmtime.a); the consumer needs no libwasmtime"
fi

# An installed layout, outside the prefix and the build tree.
mkdir -p "${T}/inst/bin" "${T}/inst/lib"
cp "${exe}" "${T}/inst/bin/"
if [ "${type}" = SHARED_LIBRARY ]; then
    cp "${lib}" "${T}/inst/lib/"
fi
out="$( (unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH
         exec "${T}/inst/bin/wasmtime_smoke") )" || fail "the installed consumer did not run"
[ "${out}" = "wasmtime answer 42" ] || fail "the consumer printed '${out}'"
ok "the installed consumer runs from bin/ + lib/ (${out})"
