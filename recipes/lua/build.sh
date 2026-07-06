#!/usr/bin/env bash
# recipes/lua/build.sh — build Lua from source using its native Makefile.
#
# Lua's upstream doesn't ship autoconf/cmake; instead its Makefile has
# per-platform targets (linux, macosx, freebsd, bsd, generic).  We use
# those directly and install with a custom prefix.  A pkg-config file
# is generated post-install (upstream doesn't provide one).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

case "$(uname -s)" in
    Linux)   target=linux-readline ;;
    Darwin)  target=macosx ;;
    FreeBSD) target=freebsd ;;
    NetBSD)  target=bsd ;;
    OpenBSD) target=bsd ;;
    *)       target=posix ;;
esac

make -j "${CVC_JOBS}" "$target" \
    MYCFLAGS="${CFLAGS:-}" \
    MYLDFLAGS="${LDFLAGS:-}"

make install \
    INSTALL_TOP="${CVC_INSTALL_DIR}" \
    INSTALL_LIB="${CVC_INSTALL_DIR}/lib" \
    INSTALL_INC="${CVC_INSTALL_DIR}/include" \
    INSTALL_MAN="${CVC_INSTALL_DIR}/share/man/man1"

# Emit a pkg-config file — Lua upstream doesn't ship one.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/lua5.4.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: Lua
Description: An extensible embeddable language
URL: https://www.lua.org/
Version: 5.4.7
Libs: -L\${libdir} -llua
Libs.private: -lm
Cflags: -I\${includedir}
PC
# Convention symlinks so consumers can `pkg-config --cflags lua`.
ln -sf lua5.4.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/lua.pc"
ln -sf lua5.4.pc "${CVC_INSTALL_DIR}/lib/pkgconfig/lua-5.4.pc"

# cvc helper (no-op for path-relative .pc we just emitted, but keeps
# the recipe consistent with the rest of the tree).
if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi
