#!/usr/bin/env bash
# recipes/libsodium/sodium-cmake-config.sh -- sourced by build.sh and
# build-wasm.sh.  Upstream installs a pkg-config file and no CMake package, so
# write one: find_package(sodium [1.0.x] CONFIG) gives the imported target
# sodium::sodium.  It finds its files relative to itself, so the bundle stays
# relocatable.  A static library's target carries SODIUM_STATIC, which the
# headers need on Windows (build.ps1 installs the same target through CMake's
# own export there) and which is harmless everywhere else.

# sodium_write_cmake_config PREFIX LIBFILE SHARED|STATIC
sodium_write_cmake_config() {
    local prefix="$1" libfile="$2" kind="$3"
    local dir="${prefix}/lib/cmake/sodium"
    local version
    version=$(sed -n 's/^#define SODIUM_VERSION_STRING "\(.*\)"$/\1/p' \
        "${prefix}/include/sodium/version.h")
    [[ -n "${version}" ]] || { echo "libsodium: no SODIUM_VERSION_STRING in version.h" >&2; return 1; }
    [[ -e "${prefix}/lib/${libfile}" ]] || { echo "libsodium: lib/${libfile} not installed" >&2; return 1; }
    mkdir -p "${dir}"
    {
        echo "# sodiumConfig.cmake -- written by cvcpkg's libsodium recipe (libsodium ${version})."
        echo "get_filename_component(_sodium_prefix \"\${CMAKE_CURRENT_LIST_DIR}/../../..\" ABSOLUTE)"
        echo "if(NOT TARGET sodium::sodium)"
        echo "  add_library(sodium::sodium ${kind} IMPORTED)"
        echo "  set_target_properties(sodium::sodium PROPERTIES"
        echo "    IMPORTED_LOCATION \"\${_sodium_prefix}/lib/${libfile}\""
        echo "    INTERFACE_INCLUDE_DIRECTORIES \"\${_sodium_prefix}/include\")"
        if [[ "${kind}" == "STATIC" ]]; then
            echo "  set_property(TARGET sodium::sodium APPEND PROPERTY"
            echo "    INTERFACE_COMPILE_DEFINITIONS SODIUM_STATIC=1)"
        fi
        echo "endif()"
        echo "set(sodium_FOUND TRUE)"
        echo "unset(_sodium_prefix)"
    } > "${dir}/sodiumConfig.cmake"
    {
        echo "# sodiumConfigVersion.cmake -- any 1.0.x at or above the one asked for."
        echo "set(PACKAGE_VERSION \"${version}\")"
        echo "set(PACKAGE_VERSION_COMPATIBLE TRUE)"
        echo "if(PACKAGE_FIND_VERSION)"
        echo "  if(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION OR"
        echo "     NOT PACKAGE_FIND_VERSION_MAJOR STREQUAL \"1\")"
        echo "    set(PACKAGE_VERSION_COMPATIBLE FALSE)"
        echo "  elseif(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)"
        echo "    set(PACKAGE_VERSION_EXACT TRUE)"
        echo "  endif()"
        echo "endif()"
    } > "${dir}/sodiumConfigVersion.cmake"
    echo "libsodium: wrote lib/cmake/sodium (${kind} lib/${libfile}, ${version})"
}
