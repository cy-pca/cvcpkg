#!/usr/bin/env bash
# recipes/luajit/build.sh — build LuaJIT 2.1 for Linux, macOS and the BSDs with
# its own Makefile (GNU make), in upstream's default "mixed" mode:
#
#   bin/luajit-2.1.<relver> (+ bin/luajit symlink)   statically linked interpreter
#   lib/libluajit-5.1.so.2.1.<relver> (+ .so.2, .so)   shared library, SONAME libluajit-5.1.so.2
#   lib/libluajit-5.1.2.1.<relver>.dylib (+ .2.dylib, .dylib) on macOS, id @rpath/libluajit-5.1.2.dylib
#   lib/libluajit-5.1.a                                static library
#   include/luajit-2.1/*.h, share/luajit-2.1/jit/*.lua, lib/pkgconfig/luajit.pc
#
# plus a CMake package (lib/cmake/luajit), which upstream does not ship.
#
# Install location vs. package.path. LuaJIT compiles its PREFIX into the
# default package.path (LUA_ROOT / LUA_LJDIR in luaconf.h) so that `luajit -b`,
# -jv, -jdump and -jp can find the jit.* modules under share/luajit-2.1. A
# cvcpkg bundle is unpacked at an arbitrary prefix, so baking the build
# prefix in would bake a builder path that exists on no consumer machine.
# This script therefore BUILDS with upstream's default PREFIX=/usr/local (no
# LUA_ROOT override, exactly what an upstream `make` produces) and only
# INSTALLS into CVC_INSTALL_DIR. To use the jit.* modules from a cvcpkg prefix
# set LUA_PATH="<prefix>/share/luajit-2.1/?.lua;;" (or extend package.path
# from the embedding program); see recipe.yaml notes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # CC, CVC_JOBS, cvc_rewrite_install_paths
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/luajit-common.sh"

cd "${CVC_SOURCE_DIR}"
luajit_detect_version

# LuaJIT's Makefiles need GNU make; the BSDs' make(1) cannot parse them.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        command -v gmake >/dev/null 2>&1 || {
            echo "luajit: GNU make (gmake) is required on $(uname -s)" >&2; exit 1; }
        MAKE=gmake
        ;;
esac

# CC on the command line: the Makefile defaults to gcc, which the BSDs' clang
# toolchains do not have. CFLAGS (-fPIC from the matrix on ELF) is appended by
# the Makefile itself, so libluajit-5.1.a can be linked into shared objects
# (Python extensions, plugins) as well as executables.
"${MAKE}" -j "${CVC_JOBS}" amalg PREFIX=/usr/local CC="${CC}"
"${MAKE}" install PREFIX="${CVC_INSTALL_DIR}"

LIB="${CVC_INSTALL_DIR}/lib"
if [ "$(uname -s)" = "Darwin" ]; then
    shared_real="libluajit-5.1.${LUAJIT_R}.dylib"
    shared_soname="@rpath/libluajit-5.1.2.dylib"
    # The Makefile sets the install name from the BUILD prefix
    # (/usr/local/lib/libluajit-5.1.2.dylib); make it @rpath-relative.
    install_name_tool -id "${shared_soname}" "${LIB}/${shared_real}"
else
    shared_real="libluajit-5.1.so.${LUAJIT_R}"
    shared_soname="libluajit-5.1.so.2"
fi
test -f "${LIB}/${shared_real}" || { echo "luajit: ${LIB}/${shared_real} was not installed" >&2; exit 1; }

# luajit.pc: `make install` wrote prefix=<CVC_INSTALL_DIR>; anchor it at the
# .pc file's own location so the prefix relocates.
sed -e 's|^prefix=.*|prefix=${pcfiledir}/../..|' "${LIB}/pkgconfig/luajit.pc" > "${LIB}/pkgconfig/luajit.pc.tmp"
mv -f "${LIB}/pkgconfig/luajit.pc.tmp" "${LIB}/pkgconfig/luajit.pc"

if [ "$(uname -s)" = "Darwin" ]; then
    luajit_write_cmake_config "lib/${shared_real}" "${shared_soname}" "lib/libluajit-5.1.a" 'm'
else
    luajit_write_cmake_config "lib/${shared_real}" "${shared_soname}" "lib/libluajit-5.1.a" 'm;${CMAKE_DL_LIBS}'
fi

cvc_rewrite_install_paths

# Smoke: the interpreter runs, and the jit.* modules shipped under
# share/luajit-2.1 load once LUA_PATH points there (-bl lists bytecode via jit.bc).
"${CVC_INSTALL_DIR}/bin/luajit" -e 'assert(jit and jit.status()); print(jit.version .. " (" .. jit.arch .. ") OK")'
LUA_PATH="${CVC_INSTALL_DIR}/share/luajit-2.1/?.lua;;" \
    "${CVC_INSTALL_DIR}/bin/luajit" -bl -e 'local x = 1' > /dev/null
