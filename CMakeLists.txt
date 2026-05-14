# libcvc-deps — packaging glue.
#
# This project does NOT build any libraries of its own; the
# third-party dependencies are obtained at workflow time from apt /
# brew / vcpkg / aqtinstall / source builds and staged into the
# release archive. This CMakeLists.txt exists solely so that:
#
#   1. The release workflow has a single VERSION source (parsed with
#      the same `awk` snippet libcvc uses).
#   2. We can install a tiny `libcvc-depsConfig.cmake` into the
#      archive at `<root>/share/cmake/libcvc-deps/`, letting
#      downstream consumers assert they're configured against a
#      known dep distribution via:
#
#          find_package(libcvc-deps CONFIG REQUIRED)
#
# Invoked from CI as:
#
#     cmake -B build -DCMAKE_INSTALL_PREFIX=<stage-dir>
#     cmake --install build
#
# …then the workflow drops apt/brew/vcpkg outputs alongside the
# installed config file before archiving.

cmake_minimum_required(VERSION 3.21)

project(libcvc-deps
  VERSION 1.0.3
  DESCRIPTION "Prebuilt third-party dependency bundle for libcvc and volrover3"
  LANGUAGES NONE)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

set(LIBCVC_DEPS_CMAKE_DIR "${CMAKE_INSTALL_DATAROOTDIR}/cmake/libcvc-deps")

# Generate the package config file from a template.
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

# Also drop a plain-text manifest at the archive root so consumers
# can quickly sanity-check which release / config they extracted.
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/MANIFEST.txt"
  "libcvc-deps ${PROJECT_VERSION}\n"
  "Build type: \${CMAKE_BUILD_TYPE}\n"
  "Generated:  \${CMAKE_GENERATOR}\n")

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/MANIFEST.txt"
  "${CMAKE_CURRENT_SOURCE_DIR}/README.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE"
  DESTINATION ".")
