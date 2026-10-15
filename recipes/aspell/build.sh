#!/usr/bin/env bash
# recipes/aspell/build.sh — build GNU Aspell via autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Some Aspell 0.60.8 sources trigger -Werror=implicit-function-declaration
# on modern clang.  Disable that so the build proceeds.
export CFLAGS="${CFLAGS:-} -Wno-error=implicit-function-declaration"
export CXXFLAGS="${CXXFLAGS:-} -Wno-error=implicit-function-declaration"

# VectorHashTable<Parms>::recalc_size() names members that do not exist
# (this->e, this->_size).  Nothing instantiates it, so gcc and older clang let
# it pass, but current clang checks the template body and every macOS build
# failed: "no member named 'e' in 'VectorHashTable<Parms>'".  Still present in
# 0.60.8.1.  Use the members it means, end() and size_.
_vh=modules/speller/default/vector_hash-t.hpp
sed -i.orig 's/i != this->e; ++i, ++this->_size/i != end(); ++i, ++size_/' "${_vh}"
rm -f "${_vh}.orig"
grep -q 'i != end(); ++i, ++size_' "${_vh}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    --disable-nls
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Emit a pkg-config file — Aspell doesn't ship one.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/aspell.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: aspell
Description: GNU spell-checker library
URL: http://aspell.net/
Version: 0.60.8
Libs: -L\${libdir} -laspell
Cflags: -I\${includedir}
PC

# Relocate installed .pc/.cmake files and bin/*-config scripts.  This script
# does not source env-*.sh, so the old `command -v` guard was always false
# and the rewrite never ran; source the helper directly.
. "$(cd "$(dirname "$0")" && pwd)/../_common/rewrite-install-paths.sh"
cvc_rewrite_install_paths
