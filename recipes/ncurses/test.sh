#!/usr/bin/env bash
# recipes/ncurses/test.sh — check the staged ncurses from a relocated prefix.
#
#  1. linux: bin/ tools load the prefix's libncursesw (RUNPATH $ORIGIN/../lib),
#     not the host's.
#  2. linux, macOS: the library searches the prefix's own share/terminfo first
#     (terminfo-relative-to-library.patch): infocmp -D lists it first, and
#     tput reads tmux-256color and xterm-256color with no TERMINFO or
#     TERMINFO_DIRS set.
# The BSDs keep the configured search list, and their RPATHs are relocated
# differently (OpenBSD's only when the bundle is installed), so there a tool
# is only run in place, with the build's library path.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- ncurses smoke test (${CVC_PLATFORM:-native}) --"

case "${CVC_PLATFORM:-}" in
    linux|macos) ;;
    *)
        "${CVC_INSTALL_DIR}/bin/infocmp" -V
        echo "-- ncurses smoke test passed (in place) --"
        exit 0
        ;;
esac

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}" "${T}/home"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
MOVED="$(cd "${MOVED}" && pwd -P)"
# The test runs with the build's prefixes on the loader path, which would hide
# what the tools' own RUNPATH finds; an install has none.  No ~/.terminfo.
unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH TERMINFO TERMINFO_DIRS
export HOME="${T}/home"

"${MOVED}/bin/infocmp" -V

if [[ "${CVC_PLATFORM:-}" == "linux" ]]; then
    for tool in infocmp tput tic toe clear tset; do
        libs="$(ldd "${MOVED}/bin/${tool}")"
        path="$(awk '$1 == "libncursesw.so.6" && $2 == "=>" { print $3 }' <<<"${libs}")"
        [[ -n "${path}" && "$(readlink -f "${path}")" == "$(readlink -f "${MOVED}/lib/libncursesw.so.6")" ]] || {
            echo "FAIL: bin/${tool} does not load the prefix's libncursesw.so.6:"
            echo "${libs}"
            exit 1
        }
    done
    echo "  OK: bin/ tools load libncursesw from the relocated prefix"
fi

case "${CVC_PLATFORM:-}" in
    linux|macos)
        first="$("${MOVED}/bin/infocmp" -D | head -n 1)"
        echo "  infocmp -D: $("${MOVED}/bin/infocmp" -D | tr '\n' ' ')"
        [[ "${first}" == "${MOVED}/share/terminfo" ]] || {
            echo "FAIL: the first terminfo directory is ${first}, not ${MOVED}/share/terminfo"
            exit 1
        }
        for term in tmux-256color xterm-256color; do
            colors="$("${MOVED}/bin/tput" -T "${term}" colors)"
            [[ "${colors}" == "256" ]] || { echo "FAIL: tput -T ${term} colors gave '${colors}'"; exit 1; }
        done
        echo "  OK: terminfo is read from the relocated prefix's share/terminfo"
        ;;
esac

echo "-- ncurses smoke test passed --"
