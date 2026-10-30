#!/usr/bin/env bash
# recipes/tmux/build.sh — build tmux against cvcpkg's libevent and ncurses.
#
# The executable goes to libexec/tmux/tmux; bin/tmux is the wrapper in this
# recipe directory (tmux.sh), which points TERMINFO_DIRS at the prefix's
# share/terminfo before running it.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${CVC_SOURCE_DIR}"

# Only the deps prefix's .pc files.  configure tries tinfow, tinfo, ncursesw
# and ncurses in that order, so a build host with its own tinfo.pc would
# otherwise link tmux against the host's libtinfo; cvcpkg's ncurses ships
# ncursesw.pc (libncursesw carries the terminfo functions).
export PKG_CONFIG_LIBDIR="${CVC_DEPS_PREFIX}/lib/pkgconfig"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --bindir="${CVC_INSTALL_DIR}/libexec/tmux"
    --disable-dependency-tracking
    # Without --with-TERM, configure picks the default terminal by asking the
    # build host's terminfo for tmux-256color, then screen-256color, then
    # screen.  The bundle always has tmux-256color: its wrapper puts
    # cvcpkg's ncurses database first.
    --with-TERM=tmux-256color
)

MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

install -d "${CVC_INSTALL_DIR}/bin"
install -m 0755 "${SCRIPT_DIR}/tmux.sh" "${CVC_INSTALL_DIR}/bin/tmux"

# The libraries it links are cvcpkg's, never the build host's.
needed=$(readelf -d "${CVC_INSTALL_DIR}/libexec/tmux/tmux" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
echo "tmux NEEDED: $(tr '\n' ' ' <<<"${needed}")"
for want in libevent_core-2.1.so.7 libncursesw.so.6; do
    grep -qx "${want}" <<<"${needed}" || {
        echo "tmux: libexec/tmux/tmux does not link ${want}" >&2
        exit 1
    }
done
if grep -q '^libtinfo' <<<"${needed}"; then
    echo "tmux: libexec/tmux/tmux links the build host's libtinfo" >&2
    exit 1
fi
