# cvcpkg-toolchain.cmake — optional CMake toolchain file.
#
# Lets downstream projects point at a cvcpkg prefix without editing
# their CMakeLists.txt:
#
#     cmake -B build \
#       -DCMAKE_TOOLCHAIN_FILE=<prefix>/share/cmake/cvcpkg/cvcpkg-toolchain.cmake
#
# Or set CVCPKG_PREFIX explicitly:
#
#     cmake -B build \
#       -DCVCPKG_PREFIX=/path/to/deps \
#       -DCMAKE_TOOLCHAIN_FILE=.../cvcpkg-toolchain.cmake
#
# This file is also installed by `cvcpkg install` into the prefix.

# Determine the prefix from this file's location or from CVCPKG_PREFIX.
if(DEFINED CVCPKG_PREFIX)
  # User provided an explicit prefix.
elseif(DEFINED CMAKE_CURRENT_LIST_DIR)
  # Derive from installed location: <prefix>/share/cmake/cvcpkg/
  get_filename_component(CVCPKG_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
else()
  message(FATAL_ERROR "Cannot determine cvcpkg prefix. Set -DCVCPKG_PREFIX=...")
endif()

# Add to search paths.
list(PREPEND CMAKE_PREFIX_PATH "${CVCPKG_PREFIX}")

# pkg-config for Meson/Autotools sub-projects.
if(IS_DIRECTORY "${CVCPKG_PREFIX}/lib/pkgconfig")
  set(ENV{PKG_CONFIG_PATH} "${CVCPKG_PREFIX}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")
endif()

message(STATUS "cvcpkg: using prefix ${CVCPKG_PREFIX}")
