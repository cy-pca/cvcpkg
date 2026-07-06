#!/usr/bin/env bash
# recipes/ncurses/build.sh — build ncurses from source using autotools.
#
# Builds both the narrow (libncurses) and wide-character (libncursesw)
# variants.  CPython needs the wide variant for its curses module and
# for readline to handle multi-byte input correctly.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Use gmake on BSDs.
MAKE=make
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
fi

# Flags common to both the narrow and wide builds.
COMMON_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --without-debug
    --without-tests
    --without-manpages
    --with-shared
    --without-static
    # Put headers in include/ncurses(w)/ AND also install compat headers
    # (curses.h, ncurses.h, term.h, termcap.h) directly into include/.
    --includedir="${CVC_INSTALL_DIR}/include/ncurses"
    --enable-pc-files
    --with-pkg-config-libdir="${CVC_INSTALL_DIR}/lib/pkgconfig"
    # Use the form/menu/panel extensions.
    --with-form-libdir="${CVC_INSTALL_DIR}/lib"
    --with-menu-libdir="${CVC_INSTALL_DIR}/lib"
    --with-panel-libdir="${CVC_INSTALL_DIR}/lib"
    # Disable gpm (Linux-only mouse daemon) to keep deps minimal.
    --without-gpm
    # Disable Ada95 bindings.
    --without-ada
    # Generate pkg-config files.
    --with-pkg-config
    # The terminfo database goes inside the prefix.
    --with-terminfo-dirs="${CVC_INSTALL_DIR}/share/terminfo:/usr/share/terminfo:/etc/terminfo"
    --with-default-terminfo-dir="${CVC_INSTALL_DIR}/share/terminfo"
)

# 1. Build the narrow (byte-at-a-time) variant.
./configure "${COMMON_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Install compat headers (curses.h → include/, ncurses.h, term.h, termcap.h).
cp -f "${CVC_INSTALL_DIR}/include/ncurses/curses.h"  "${CVC_INSTALL_DIR}/include/curses.h"  2>/dev/null || true
cp -f "${CVC_INSTALL_DIR}/include/ncurses/ncurses.h" "${CVC_INSTALL_DIR}/include/ncurses.h" 2>/dev/null || true
cp -f "${CVC_INSTALL_DIR}/include/ncurses/term.h"    "${CVC_INSTALL_DIR}/include/term.h"    2>/dev/null || true
cp -f "${CVC_INSTALL_DIR}/include/ncurses/termcap.h" "${CVC_INSTALL_DIR}/include/termcap.h" 2>/dev/null || true

# 2. Build the wide-character (libncursesw) variant — CPython prefers this.
$MAKE clean
./configure "${COMMON_ARGS[@]}" \
    --enable-widec \
    --includedir="${CVC_INSTALL_DIR}/include/ncursesw"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Provide compat symlinks so code that links -lncurses without -lncursesw
# still works on systems that don't have a non-wide ncurses.
cd "${CVC_INSTALL_DIR}/lib"
for lib in ncurses form menu panel; do
    for suf in .so .a "$(ls lib${lib}w.so.* 2>/dev/null | head -1 | sed 's/lib.*w//' || true)"; do
        [[ -e "lib${lib}${suf}" ]] || ln -sf "lib${lib}w${suf}" "lib${lib}${suf}" 2>/dev/null || true
    done
done
