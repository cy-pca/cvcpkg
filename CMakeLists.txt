# cvcpkg — CMake integration for cvcpkg-managed dependency prefixes.
#
# This project does NOT build any libraries of its own.  Dependencies
# are obtained via `cvcpkg install` (prebuilt binary packages) or built
# from source recipes.  This CMakeLists.txt installs:
#
#   1. cvcpkgConfig.cmake — so downstream projects can:
#
#          find_package(cvcpkg CONFIG REQUIRED)
#          find_package(Boost REQUIRED)      # resolves in the same prefix
#
#   2. libcvc-depsConfig.cmake — backward-compat alias (wraps cvcpkg).
#
#   3. A MANIFEST.txt for quick identification of the install.
#
# Usage:
#
#     cmake -B build -DCMAKE_INSTALL_PREFIX=<deps-prefix>
#     cmake --install build
#
# Or, after `cvcpkg install`, simply point CMAKE_PREFIX_PATH at the
# prefix directory (the activate script does this automatically).

cmake_minimum_required(VERSION 3.21)

project(cvcpkg
  VERSION 2.0.2
  DESCRIPTION "Cross-platform binary package archive for scientific computing"
  LANGUAGES NONE)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# ── Primary: cvcpkgConfig.cmake ──────────────────────────────

set(CVCPKG_CMAKE_DIR "${CMAKE_INSTALL_DATAROOTDIR}/cmake/cvcpkg")

configure_package_config_file(
  "${CMAKE_CURRENT_SOURCE_DIR}/cmake/cvcpkgConfig.cmake.in"
  "${CMAKE_CURRENT_BINARY_DIR}/cvcpkgConfig.cmake"
  INSTALL_DESTINATION "${CVCPKG_CMAKE_DIR}"
  NO_SET_AND_CHECK_MACRO
  NO_CHECK_REQUIRED_COMPONENTS_MACRO)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/cvcpkgConfigVersion.cmake"
  VERSION   "${PROJECT_VERSION}"
  COMPATIBILITY SameMinorVersion)

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/cvcpkgConfig.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/cvcpkgConfigVersion.cmake"
  "${CMAKE_CURRENT_SOURCE_DIR}/cmake/cvcpkg-toolchain.cmake"
  DESTINATION "${CVCPKG_CMAKE_DIR}")

# ── Backward compat: libcvc-depsConfig.cmake ─────────────────

set(LIBCVC_DEPS_CMAKE_DIR "${CMAKE_INSTALL_DATAROOTDIR}/cmake/libcvc-deps")

configure_package_config_file(
  "${CMAKE_CURRENT_SOURCE_DIR}/cmake/libcvc-depsConfig.cmake.in"
  "${CMAKE_CURRENT_BINARY_DIR}/libcvc-depsConfig.cmake"
  INSTALL_DESTINATION "${LIBCVC_DEPS_CMAKE_DIR}"
  NO_SET_AND_CHECK_MACRO
  NO_CHECK_REQUIRED_COMPONENTS_MACRO)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/libcvc-depsConfigVersion.cmake"
  VERSION   "${PROJECT_VERSION}"
  COMPATIBILITY SameMinorVersion)

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/libcvc-depsConfig.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/libcvc-depsConfigVersion.cmake"
  DESTINATION "${LIBCVC_DEPS_CMAKE_DIR}")

# ── Manifest + docs ──────────────────────────────────────────

file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/MANIFEST.txt"
  "cvcpkg ${PROJECT_VERSION}\n"
  "Build type: \${CMAKE_BUILD_TYPE}\n"
  "Generated:  \${CMAKE_GENERATOR}\n")

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/MANIFEST.txt"
  "${CMAKE_CURRENT_SOURCE_DIR}/README.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE"
  DESTINATION ".")
