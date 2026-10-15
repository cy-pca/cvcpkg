#!/usr/bin/env bash
# recipes/readline/build.sh — build GNU Readline from source using autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --with-curses
)

# Respect static/shared link mode.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# Link the cvcpkg ncurses (its wide-char libncursesw, which carries the
# termcap functions), never the build host's.  Before cvc.5 nothing named it:
# make's SHLIB_LIBS fell back to the host's -ltinfo, so libreadline.so NEEDED
# the build machine's libtinfo.so.6, while configure, probing the host on its
# own, wrote "Requires.private: ncurses" into readline.pc -- a .pc no cvcpkg
# package ships (cvcpkg's ncurses installs ncursesw.pc).  pkg-config therefore
# rejected readline in every consumer: postgresql's meson (-Dreadline=enabled)
# failed on linux, linux/arm64 and macOS with "library 'libedit' not found".
# bash_cv_termcap_lib picks the library (TERMCAP_LIB=-lncursesw, and
# readline.pc's Requires.private becomes ncursesw), and
# --with-shared-termcap-library links libreadline.so against it.
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
CONFIGURE_ARGS+=(bash_cv_termcap_lib=libncursesw --with-shared-termcap-library)

./configure "${CONFIGURE_ARGS[@]}"

make -j "${CVC_JOBS}"
make install

# Relocate installed .pc/.cmake files and bin/*-config scripts, which keep this
# build's temporary install dir otherwise (recipes/_common/rewrite-install-paths.sh).
. "$(cd "$(dirname "$0")" && pwd)/../_common/rewrite-install-paths.sh"
cvc_rewrite_install_paths

grep -q '^Requires.private: ncursesw$' "${CVC_INSTALL_DIR}/lib/pkgconfig/readline.pc" || {
    echo "readline: readline.pc does not require ncursesw:" >&2
    cat "${CVC_INSTALL_DIR}/lib/pkgconfig/readline.pc" >&2
    exit 1
}
