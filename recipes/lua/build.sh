#!/usr/bin/env bash
# recipes/lua/build.sh — build Lua for Linux, macOS and the BSDs: the shared
# AND the static library, the lua/luac executables, pkg-config files and a
# CMake package.
#
# Lua's upstream Makefile has per-platform targets (linux-readline, macosx,
# freebsd, bsd, posix) and builds only the static liblua.a. Embedders want a
# shared library too, so after the upstream build this script links one out of
# the same objects (compiled -fPIC via the matrix CFLAGS; Mach-O is always PIC),
# which is what the Linux distributions do:
#
#   Linux/BSD  lib/liblua.so.5.4.9, SONAME liblua.so.5.4,
#              + liblua.so.5.4 -> liblua.so.5.4.9 and liblua.so -> liblua.so.5.4
#   macOS      lib/liblua.5.4.9.dylib, install name @rpath/liblua.5.4.dylib,
#              + liblua.5.4.dylib and liblua.dylib symlinks
#
# bin/lua is relinked against the shared library (RUNPATH $ORIGIN/../lib,
# @loader_path/../lib on macOS), so C modules it loads with require() and the
# interpreter share one copy of the Lua runtime. bin/luac stays statically
# linked, as upstream builds it: it calls Lua-internal functions (luaU_dump,
# luaF_*, ...) that luaconf.h marks hidden in a shared library on ELF.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # CC, CVC_JOBS, cvc_rewrite_install_paths
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lua-common.sh"

cd "${CVC_SOURCE_DIR}"
lua_detect_version

# Upstream target, plus the libraries that target links lua with (its SYSLIBS),
# repeated below for the relink against the shared library.
case "$(uname -s)" in
    Linux)   target=linux-readline; lua_syslibs="-Wl,-E -ldl -lreadline"; dl="-ldl" ;;
    Darwin)  target=macosx;         lua_syslibs="-lreadline";             dl="" ;;
    FreeBSD) target=freebsd;        lua_syslibs="-Wl,-E -ledit";          dl="" ;;
    NetBSD)  target=bsd;            lua_syslibs="-Wl,-E";                 dl="" ;;
    OpenBSD) target=bsd;            lua_syslibs="-Wl,-E";                 dl="" ;;
    *)       target=posix;          lua_syslibs="";                       dl="" ;;
esac

# readline is a runtime dependency (lua's line editing): take its headers and
# library from the cvcpkg prefix, not from whatever the build host has.
dep_cflags=""
dep_ldflags=""
if [ -n "${CVC_DEPS_PREFIX:-}" ]; then
    [ -d "${CVC_DEPS_PREFIX}/include" ] && dep_cflags="-I${CVC_DEPS_PREFIX}/include"
    [ -d "${CVC_DEPS_PREFIX}/lib" ] && dep_ldflags="-L${CVC_DEPS_PREFIX}/lib"
fi

# CC is passed explicitly: the Makefile hard-codes `gcc -std=gnu99`, and the
# BSDs' env scripts select clang (OpenBSD has no gcc in base).
make -j "${CVC_JOBS}" "${target}" \
    CC="${CC} -std=gnu99" \
    MYCFLAGS="${CFLAGS:-} ${dep_cflags}" \
    MYLDFLAGS="${LDFLAGS:-} ${dep_ldflags}"

# ── Shared library from the liblua.a objects ─────────────────────────────────
cd src
lib_objs=()
for o in *.o; do
    case "${o}" in
        lua.o|luac.o) ;;            # the two programs' entry points
        *) lib_objs+=("${o}") ;;
    esac
done

if [ "$(uname -s)" = "Darwin" ]; then
    shared_real="liblua.${LUA_R}.dylib"
    shared_soname="liblua.${LUA_V}.dylib"
    shared_links=("liblua.${LUA_V}.dylib:${shared_real}" "liblua.dylib:liblua.${LUA_V}.dylib")
    # shellcheck disable=SC2086
    ${CC} -dynamiclib -o "${shared_real}" \
        -install_name "@rpath/${shared_soname}" \
        -compatibility_version "${LUA_V}" -current_version "${LUA_R}" \
        ${LDFLAGS:-} ${lib_objs[@]+"${lib_objs[@]}"}
    lua_rpath="-Wl,-rpath,@loader_path/../lib"
else
    shared_real="liblua.so.${LUA_R}"
    shared_soname="liblua.so.${LUA_V}"
    shared_links=("liblua.so.${LUA_V}:${shared_real}" "liblua.so:liblua.so.${LUA_V}")
    # shellcheck disable=SC2086
    ${CC} -shared -o "${shared_real}" -Wl,-soname,"${shared_soname}" \
        ${LDFLAGS:-} ${lib_objs[@]+"${lib_objs[@]}"} -lm ${dl}
    lua_rpath="-Wl,-rpath,\$ORIGIN/../lib"
fi

# Relink lua against the shared library (by path, so the static liblua.a next
# to it can never be picked instead); the result replaces the static lua that
# `make install` copies.
# shellcheck disable=SC2086
${CC} -o lua.shared ${LDFLAGS:-} ${dep_ldflags} lua.o "./${shared_real}" \
    "${lua_rpath}" -lm ${lua_syslibs}
mv -f lua.shared lua
cd ..

make install \
    INSTALL_TOP="${CVC_INSTALL_DIR}" \
    INSTALL_LIB="${CVC_INSTALL_DIR}/lib" \
    INSTALL_INC="${CVC_INSTALL_DIR}/include" \
    INSTALL_MAN="${CVC_INSTALL_DIR}/share/man/man1"

cp -p "src/${shared_real}" "${CVC_INSTALL_DIR}/lib/${shared_real}"
for pair in "${shared_links[@]}"; do
    ln -sf "${pair#*:}" "${CVC_INSTALL_DIR}/lib/${pair%%:*}"
done

# ── pkg-config + CMake package ──────────────────────────────────────────────
lua_write_pkgconfig "An extensible embeddable language" "-lm${dl:+ ${dl}}"
if [ "$(uname -s)" = "Darwin" ]; then
    lua_write_cmake_config "lib/${shared_real}" "@rpath/${shared_soname}" "lib/liblua.a" 'm'
else
    lua_write_cmake_config "lib/${shared_real}" "${shared_soname}" "lib/liblua.a" 'm;${CMAKE_DL_LIBS}'
fi

cvc_rewrite_install_paths

# The interpreter must run against the library it was just relinked to.
"${CVC_INSTALL_DIR}/bin/lua" -e 'assert(_VERSION == "Lua '"${LUA_V}"'"); print(_VERSION .. " shared OK")'
"${CVC_INSTALL_DIR}/bin/luac" -v
