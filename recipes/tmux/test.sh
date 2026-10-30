#!/usr/bin/env bash
# recipes/tmux/test.sh — smoke test the staged tmux from a relocated prefix.
#
#  1. bin/tmux -V reports the pinned version.
#  2. The real binary loads libevent and ncurses from the prefix, not the host.
#  3. A detached session on a private server runs a command in a 100x30 pane,
#     and capture-pane returns what it printed.
#  4. The default terminal is tmux-256color, and the pane inherits a
#     TERMINFO_DIRS that starts with the prefix's share/terminfo, which has it.
#  5. A client attaches from a pseudo-terminal (util-linux script), which reads
#     terminfo for its TERM, and detaches again.
# The prefix is tmux plus its runtime closure, copied where it was not built.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"

echo "-- tmux smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
MOVED="${T}/relocated/prefix"
TMUX_BIN="${MOVED}/bin/tmux"
sock="cvcpkg-tmux-test-$$"
cleanup() {
    "${TMUX_BIN}" -L "${sock}" kill-server 2>/dev/null || true
    rm -rf "${T}"
}
trap cleanup EXIT

mkdir -p "${MOVED}"
cp -pR "${CVC_DEPS_PREFIX}/." "${MOVED}/"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
# The test runs with LD_LIBRARY_PATH on the build's prefixes, which would hide
# whether the binary's own RUNPATH finds the libraries; an install has none.
unset LD_LIBRARY_PATH TMUX TERMINFO TERMINFO_DIRS
export LC_ALL=C.UTF-8

ver="$("${TMUX_BIN}" -V)"
echo "  ${ver}"
[[ "${ver}" == "tmux 3.8" ]] || { echo "FAIL: tmux version"; exit 1; }

libs="$(ldd "${MOVED}/libexec/tmux/tmux")"
for lib in libevent_core-2.1.so.7 libncursesw.so.6; do
    # ldd prints the path as the RUNPATH spells it (libexec/tmux/../../lib).
    path="$(awk -v l="${lib}" '$1 == l && $2 == "=>" { print $3 }' <<<"${libs}")"
    [[ -n "${path}" && "$(readlink -f "${path}")" == "$(readlink -f "${MOVED}/lib/${lib}")" ]] || {
        echo "FAIL: ${lib} is not loaded from the prefix:"
        echo "${libs}"
        exit 1
    }
done
echo "  OK: libevent_core and libncursesw load from the relocated prefix"

tm() { "${TMUX_BIN}" -L "${sock}" -f /dev/null "$@"; }
tm new-session -d -s smoke -x 100 -y 30 \
    'printf "hello from tmux\n"; printf "TERM=%s\n" "$TERM"; printf "DIRS=%s\n" "$TERMINFO_DIRS"; exec sleep 60'
screen=
for _ in $(seq 1 50); do
    screen="$(tm capture-pane -p -t smoke)"
    grep -q '^DIRS=' <<<"${screen}" && break
    sleep 0.1
done
echo "${screen}" | sed '/^$/d; s/^/  | /'
grep -qx 'hello from tmux' <<<"${screen}" || { echo "FAIL: the pane's output was not captured"; exit 1; }
[[ "$(tm display-message -p -t smoke '#{window_width}x#{window_height}')" == "100x30" ]] \
    || { echo "FAIL: the window is not 100x30"; exit 1; }
echo "  OK: a detached 100x30 session ran a command and capture-pane read it"

[[ "$(tm show-options -gv default-terminal)" == "tmux-256color" ]] \
    || { echo "FAIL: default-terminal is not tmux-256color"; exit 1; }
grep -qx 'TERM=tmux-256color' <<<"${screen}" || { echo "FAIL: the pane's TERM"; exit 1; }
want_dirs="DIRS=$(cd "${MOVED}" && pwd -P)/share/terminfo"
grep -qx "${want_dirs}" <<<"${screen}" || { echo "FAIL: the pane's TERMINFO_DIRS is not ${want_dirs#DIRS=}"; exit 1; }
[[ -f "${MOVED}/share/terminfo/t/tmux-256color" ]] || { echo "FAIL: no share/terminfo/t/tmux-256color"; exit 1; }
echo "  OK: TERM=tmux-256color, found in the prefix's share/terminfo"

if command -v script >/dev/null 2>&1; then
    out="$(TERM=xterm-256color timeout 30 script -qec \
        "'${TMUX_BIN}' -L '${sock}' attach-session -t smoke \\; detach-client" /dev/null </dev/null 2>&1)" || {
        echo "FAIL: a client could not attach:"
        echo "${out}"
        exit 1
    }
    grep -q 'detached' <<<"${out}" || { echo "FAIL: the client did not detach: ${out}"; exit 1; }
    echo "  OK: a client attached from a pty (TERM=xterm-256color) and detached"
else
    echo "  skipped: no script(1) for the attach test"
fi

echo "-- tmux smoke test passed --"
