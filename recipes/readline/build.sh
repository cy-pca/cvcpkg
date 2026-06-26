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

./configure "${CONFIGURE_ARGS[@]}"

# Ensure libreadline.so links against the curses/termcap library that
# provides tgetent/tgoto/tputs/etc.  Without this, downstream consumers
# (e.g. libpq's psql) fail to link against our libreadline.so with
# "undefined reference to `tgetent'" errors.  On modern Linux these
# symbols live in libtinfo (split out from ncurses); BSD/macOS keep
# them in libncurses itself.
SHLIB_LIBS=""
if command -v pkg-config >/dev/null 2>&1; then
    if pkg-config --exists tinfo 2>/dev/null; then
        SHLIB_LIBS="$(pkg-config --libs tinfo)"
    elif pkg-config --exists ncursesw 2>/dev/null; then
        SHLIB_LIBS="$(pkg-config --libs ncursesw)"
    elif pkg-config --exists ncurses 2>/dev/null; then
        SHLIB_LIBS="$(pkg-config --libs ncurses)"
    fi
fi
if [[ -z "${SHLIB_LIBS}" ]]; then
    case "$(uname)" in
        Linux)  SHLIB_LIBS="-ltinfo" ;;
        *)      SHLIB_LIBS="-lncurses" ;;
    esac
fi

make -j "${CVC_JOBS}" SHLIB_LIBS="${SHLIB_LIBS}"
make install
