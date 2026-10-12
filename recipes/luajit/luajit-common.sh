#!/usr/bin/env bash
# recipes/luajit/luajit-common.sh — helpers for luajit's build.sh. Sourced, never run.

# luajit_detect_version — LuaJIT 2.1 is a rolling release whose version is
# 2.1.<commit time>: the Makefile reads the commit timestamp from git, or, in a
# `git archive` tarball such as the pinned GitHub archive, from the .relver
# file export-subst filled in. Sets LUAJIT_MM ("2.1"), LUAJIT_RELVER and
# LUAJIT_R ("2.1.<relver>").
luajit_detect_version() {
    local maj min
    maj=$(sed -n 's/^MAJVER=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "${CVC_SOURCE_DIR}/Makefile")
    min=$(sed -n 's/^MINVER=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "${CVC_SOURCE_DIR}/Makefile")
    LUAJIT_RELVER=$(tr -d ' \r\n' < "${CVC_SOURCE_DIR}/.relver" 2>/dev/null || true)
    case "${LUAJIT_RELVER}" in
        ''|*[!0-9]*)
            echo "luajit: .relver is '${LUAJIT_RELVER}', not a commit timestamp — the source must be a git-archive tarball (GitHub archive URL) or a git checkout" >&2
            return 1 ;;
    esac
    if [ -z "${maj}" ] || [ -z "${min}" ]; then
        echo "luajit: cannot read MAJVER/MINVER from the Makefile" >&2
        return 1
    fi
    LUAJIT_MM="${maj}.${min}"
    LUAJIT_R="${LUAJIT_MM}.${LUAJIT_RELVER}"
    if [ -n "${CVC_VERSION:-}" ] && [ "${CVC_VERSION}" != "${LUAJIT_R}" ]; then
        echo "luajit: recipe upstream_version ${CVC_VERSION} != source version ${LUAJIT_R}" >&2
        return 1
    fi
    echo "luajit: version ${LUAJIT_R}"
}

# luajit_write_cmake_config SHARED SONAME STATIC STATIC_LINK_LIBS
#
# lib/cmake/luajit/luajit-config.cmake + luajit-config-version.cmake:
#
#   find_package(luajit 2.1 CONFIG REQUIRED)
#   target_link_libraries(app PRIVATE luajit::luajit)   # or luajit::luajit_static
#
# Paths are prefix-relative; STATIC_LINK_LIBS is written verbatim (it may
# reference ${CMAKE_DL_LIBS}, evaluated in the consumer's configure).
luajit_write_cmake_config() {
    local shared="$1" soname="$2" static="$3" static_libs="$4"
    local dir="${CVC_INSTALL_DIR}/lib/cmake/luajit"
    mkdir -p "${dir}"
    cat > "${dir}/luajit-config.cmake" <<EOF
# luajit-config.cmake -- CMake package for LuaJIT ${LUAJIT_R}, written by the
# cvcpkg luajit recipe (recipes/luajit/luajit-common.sh); upstream ships none.
#
#   find_package(luajit ${LUAJIT_MM} CONFIG REQUIRED)
#   target_link_libraries(app PRIVATE luajit::luajit)          # shared libluajit-5.1
#   target_link_libraries(app PRIVATE luajit::luajit_static)   # static libluajit-5.1.a
#
# LuaJIT implements the Lua 5.1 language and C API (plus its FFI, jit.* and
# bit libraries); its headers live in include/luajit-${LUAJIT_MM}, apart from
# the cvcpkg lua recipe's 5.4 headers in include/. Every path below is derived
# from this file's own location, so the prefix stays relocatable.
get_filename_component(_luajit_prefix "\${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

set(LUAJIT_VERSION_STRING "${LUAJIT_R}")
set(LUAJIT_INCLUDE_DIR "\${_luajit_prefix}/include/luajit-${LUAJIT_MM}")

if(NOT TARGET luajit::luajit_static)
  add_library(luajit::luajit_static STATIC IMPORTED)
  set_target_properties(luajit::luajit_static PROPERTIES
    IMPORTED_LOCATION "\${_luajit_prefix}/${static}"
    IMPORTED_LINK_INTERFACE_LANGUAGES "C"
    INTERFACE_INCLUDE_DIRECTORIES "\${_luajit_prefix}/include/luajit-${LUAJIT_MM}"
    INTERFACE_LINK_LIBRARIES "${static_libs}")
endif()

if(NOT TARGET luajit::luajit)
  add_library(luajit::luajit SHARED IMPORTED)
  set_target_properties(luajit::luajit PROPERTIES
    IMPORTED_LOCATION "\${_luajit_prefix}/${shared}"
    IMPORTED_SONAME "${soname}"
    INTERFACE_INCLUDE_DIRECTORIES "\${_luajit_prefix}/include/luajit-${LUAJIT_MM}")
endif()

set(LUAJIT_LIBRARIES luajit::luajit)
set(LUAJIT_FOUND TRUE)
unset(_luajit_prefix)
EOF
    luajit_write_cmake_version_file "${dir}/luajit-config-version.cmake"
}

# luajit_write_cmake_version_file PATH — SameMinorVersion: a 2.1 request takes
# any 2.1.<relver> at or above it; 2.0 and a future 2.2/3.x do not match.
luajit_write_cmake_version_file() {
    local maj="${LUAJIT_MM%%.*}" min="${LUAJIT_MM#*.}"
    cat > "$1" <<EOF
# luajit-config-version.cmake -- written by the cvcpkg luajit recipe.
set(PACKAGE_VERSION "${LUAJIT_R}")
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
elseif(PACKAGE_FIND_VERSION_MAJOR STREQUAL "${maj}" AND PACKAGE_FIND_VERSION_MINOR STREQUAL "${min}")
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
else()
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
endif()
EOF
}
