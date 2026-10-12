#!/usr/bin/env bash
# recipes/lua/lua-common.sh — helpers shared by lua's Unix build scripts
# (build.sh, build-wasm.sh, build-wasi.sh, build-cosmo.sh). Sourced, never run.
#
# Lua upstream ships neither a pkg-config file nor a CMake package, so every
# build script writes both from the templates below. The version comes from the
# source tree (src/lua.h), not from a literal repeated in each script — the old
# per-script "Version: 5.4.7" lines are exactly what a version bump forgets.

# lua_detect_version — read LUA_VERSION_{MAJOR,MINOR,RELEASE} out of src/lua.h.
# Sets LUA_MAJ / LUA_MIN / LUA_REL, LUA_V ("5.4") and LUA_R ("5.4.9").
lua_detect_version() {
    local h="${CVC_SOURCE_DIR}/src/lua.h"
    LUA_MAJ=$(sed -n 's/^#define LUA_VERSION_MAJOR[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' "$h")
    LUA_MIN=$(sed -n 's/^#define LUA_VERSION_MINOR[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' "$h")
    LUA_REL=$(sed -n 's/^#define LUA_VERSION_RELEASE[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' "$h")
    if [ -z "${LUA_MAJ}" ] || [ -z "${LUA_MIN}" ] || [ -z "${LUA_REL}" ]; then
        echo "lua: cannot read the version out of ${h}" >&2
        return 1
    fi
    LUA_V="${LUA_MAJ}.${LUA_MIN}"
    LUA_R="${LUA_V}.${LUA_REL}"
    # The recipe pins the tarball; a mismatch means upstream_version and the
    # source drifted apart, and every generated file would carry the wrong one.
    if [ -n "${CVC_VERSION:-}" ] && [ "${CVC_VERSION}" != "${LUA_R}" ]; then
        echo "lua: recipe upstream_version ${CVC_VERSION} != src/lua.h ${LUA_R}" >&2
        return 1
    fi
    echo "lua: version ${LUA_R}"
}

# lua_write_pkgconfig DESCRIPTION LIBS_PRIVATE
#
# lib/pkgconfig/lua5.4.pc plus the other names consumers probe for (lua.pc,
# lua-5.4.pc, lua54.pc — lupa's setup.py and CMake's FindLua try all of them).
# prefix is ${pcfiledir}-relative, so the file relocates with the prefix.
# V/R/INSTALL_LMOD/INSTALL_CMOD are the variables Lua's own etc/lua.pc used to
# carry and that LuaRocks still reads.
lua_write_pkgconfig() {
    local desc="$1" private="$2"
    local pcdir="${CVC_INSTALL_DIR}/lib/pkgconfig"
    local pc="${pcdir}/lua${LUA_V}.pc"
    mkdir -p "${pcdir}"
    {
        printf 'V=%s\n' "${LUA_V}"
        printf 'R=%s\n' "${LUA_R}"
        printf '%s\n' \
            'prefix=${pcfiledir}/../..' \
            'exec_prefix=${prefix}' \
            'libdir=${exec_prefix}/lib' \
            'includedir=${prefix}/include' \
            'INSTALL_LMOD=${prefix}/share/lua/${V}' \
            'INSTALL_CMOD=${prefix}/lib/lua/${V}' \
            ''
        printf 'Name: Lua\n'
        printf 'Description: %s\n' "${desc}"
        printf 'URL: https://www.lua.org/\n'
        printf 'Version: %s\n' "${LUA_R}"
        printf '%s\n' 'Libs: -L${libdir} -llua'
        if [ -n "${private}" ]; then
            printf 'Libs.private: %s\n' "${private}"
        fi
        printf '%s\n' 'Cflags: -I${includedir}'
    } > "${pc}"
    ln -sf "lua${LUA_V}.pc" "${pcdir}/lua.pc"
    ln -sf "lua${LUA_V}.pc" "${pcdir}/lua-${LUA_V}.pc"
    ln -sf "lua${LUA_V}.pc" "${pcdir}/lua${LUA_MAJ}${LUA_MIN}.pc"
}

# lua_write_cmake_config SHARED SONAME STATIC STATIC_LINK_LIBS
#
# lib/cmake/lua/lua-config.cmake + lua-config-version.cmake, so a consumer does
#
#   find_package(lua 5.4 CONFIG REQUIRED)
#   target_link_libraries(app PRIVATE lua::lua)         # or lua::lua_static
#
# SHARED / STATIC are paths relative to the prefix ("" when the platform has
# none); SONAME is the shared library's SONAME / install name. STATIC_LINK_LIBS
# is the ';'-list a static link needs on top of liblua.a, written verbatim (so
# it may reference ${CMAKE_DL_LIBS}, evaluated in the consumer's configure).
#
# The lowercase lua-config.cmake spelling is deliberate: CMake looks for both
# <Name>Config.cmake and <lowercase-name>-config.cmake, so this one file answers
# find_package(lua CONFIG) and find_package(Lua CONFIG) alike.
lua_write_cmake_config() {
    local shared="$1" soname="$2" static="$3" static_libs="$4"
    local dir="${CVC_INSTALL_DIR}/lib/cmake/lua"
    mkdir -p "${dir}"
    {
        cat <<EOF
# lua-config.cmake -- CMake package for Lua ${LUA_R}, written by the cvcpkg lua
# recipe (recipes/lua/lua-common.sh); Lua upstream ships no CMake package.
#
#   find_package(lua ${LUA_V} CONFIG REQUIRED)
#   target_link_libraries(app PRIVATE lua::lua)          # shared liblua
#   target_link_libraries(app PRIVATE lua::lua_static)   # static liblua
#
# lua::lua is the shared library where this platform build has one, and links
# lua::lua_static where it does not (wasm, wasi, cosmo). Every path below is
# derived from this file's own location, so the prefix stays relocatable.
get_filename_component(_lua_prefix "\${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

# FindLua-compatible result variables, for projects written against them.
set(LUA_VERSION_STRING "${LUA_R}")
set(LUA_VERSION_MAJOR ${LUA_MAJ})
set(LUA_VERSION_MINOR ${LUA_MIN})
set(LUA_VERSION_PATCH ${LUA_REL})
set(LUA_INCLUDE_DIR "\${_lua_prefix}/include")

EOF
        if [ -n "${static}" ]; then
            cat <<EOF
if(NOT TARGET lua::lua_static)
  add_library(lua::lua_static STATIC IMPORTED)
  set_target_properties(lua::lua_static PROPERTIES
    IMPORTED_LOCATION "\${_lua_prefix}/${static}"
    IMPORTED_LINK_INTERFACE_LANGUAGES "C"
    INTERFACE_INCLUDE_DIRECTORIES "\${_lua_prefix}/include"
    INTERFACE_LINK_LIBRARIES "${static_libs}")
endif()

EOF
        fi
        if [ -n "${shared}" ]; then
            cat <<EOF
if(NOT TARGET lua::lua)
  add_library(lua::lua SHARED IMPORTED)
  set_target_properties(lua::lua PROPERTIES
    IMPORTED_LOCATION "\${_lua_prefix}/${shared}"
    IMPORTED_SONAME "${soname}"
    INTERFACE_INCLUDE_DIRECTORIES "\${_lua_prefix}/include")
endif()

EOF
        else
            cat <<'EOF'
if(NOT TARGET lua::lua)
  add_library(lua::lua INTERFACE IMPORTED)
  set_target_properties(lua::lua PROPERTIES INTERFACE_LINK_LIBRARIES lua::lua_static)
endif()

EOF
        fi
        cat <<'EOF'
set(LUA_LIBRARY lua::lua)
set(LUA_LIBRARIES lua::lua)
set(LUA_FOUND TRUE)
unset(_lua_prefix)
EOF
    } > "${dir}/lua-config.cmake"

    lua_write_cmake_version_file "${dir}/lua-config-version.cmake"
}

# lua_write_cmake_version_file PATH
#
# Lua's C API and ABI are only stable within one 5.x minor series, so this is
# CMake's SameMinorVersion rule: find_package(lua 5.4) accepts any 5.4.y at or
# above the request and rejects 5.3 and 5.5. Version ranges are honoured too.
lua_write_cmake_version_file() {
    cat > "$1" <<EOF
# lua-config-version.cmake -- written by the cvcpkg lua recipe. Lua ${LUA_V}.x
# is ABI-compatible only within the ${LUA_V} series (SameMinorVersion).
set(PACKAGE_VERSION "${LUA_R}")
if(NOT PACKAGE_FIND_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
elseif(PACKAGE_FIND_VERSION_RANGE)
  if((PACKAGE_FIND_VERSION_RANGE_MIN STREQUAL "INCLUDE" AND PACKAGE_VERSION VERSION_LESS PACKAGE_FIND_VERSION_MIN)
     OR (PACKAGE_FIND_VERSION_RANGE_MAX STREQUAL "INCLUDE" AND PACKAGE_VERSION VERSION_GREATER PACKAGE_FIND_VERSION_MAX)
     OR (PACKAGE_FIND_VERSION_RANGE_MAX STREQUAL "EXCLUDE" AND PACKAGE_VERSION VERSION_GREATER_EQUAL PACKAGE_FIND_VERSION_MAX))
    set(PACKAGE_VERSION_COMPATIBLE FALSE)
  else()
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
  endif()
elseif(PACKAGE_VERSION VERSION_LESS PACKAGE_FIND_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
elseif(PACKAGE_FIND_VERSION_MAJOR STREQUAL "${LUA_MAJ}" AND PACKAGE_FIND_VERSION_MINOR STREQUAL "${LUA_MIN}")
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
else()
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
endif()
EOF
}
