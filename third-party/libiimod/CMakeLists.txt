# libiimod — IMOD's image-file I/O library, built from the official
# upstream source at https://github.com/LabShare-Archive/IMOD (read-only
# archive; pinned to a fixed commit).
#
# Upstream's build system is GNU make + a custom 'setup' script. We
# don't use that. Instead we compile the curated subset of source
# files that libcvc has historically vendored, install them as a
# single static library, and ship a CMake config so libcvc (and any
# other consumer) can do `find_package(libiimod CONFIG REQUIRED)`.
#
# Files pulled from upstream:
#   libiimod/  iilikemrc.c iimage.c iimrc.c iitif.c mrcfiles.c
#              mrcsec.c mrcslice.c plist.c sliceproc.c diffusion.c
#   libcfshr/  b3dutil.c ilist.c islice.c parallelwrite.c
#   include/   b3dutil.h cfsemshare.h hvemtypes.h iimage.h ilist.h
#              mrcc.h mrcfiles.h mrcslice.h sliceproc.h
# imodconfig.h is shipped locally (upstream generates it from the
# setup script; we ship a fixed copy matching libcvc's historic
# build configuration).
#
# TIFF support is ENABLED: iitif.c is compiled against the system
# libtiff discovered via `find_package(TIFF REQUIRED)`. The upstream
# `tiffstub.c` / `notiff*.h` files are NOT used — those are only
# relevant when building without libtiff, which we no longer do.

cmake_minimum_required(VERSION 3.16)
project(libiimod
  VERSION 4.0.6
  DESCRIPTION "IMOD image-file I/O library (curated subset)"
  LANGUAGES C
)

include(FetchContent)
include(CMakePackageConfigHelpers)
include(GNUInstallDirs)
include(TestBigEndian)

# libtiff is a hard dependency — iitif.c (which provides the TIFF
# read/write paths used by libcvc) #include's <tiffio.h> and calls
# TIFFOpen/TIFFGetField/... directly when NOTIFFLIBS is not defined.
# ZLIB must be found first: TIFF's installed cmake config references
# ZLIB::ZLIB as a transitive dependency (for static builds).
find_package(ZLIB REQUIRED)
find_package(TIFF REQUIRED)

# Pinned to LabShare-Archive/IMOD master HEAD as of 2026-05-13.
# The repo is archived (read-only since 2018-07-15) so this commit
# is stable and will not change.
set(LIBIIMOD_UPSTREAM_GIT  "https://github.com/LabShare-Archive/IMOD.git")
set(LIBIIMOD_UPSTREAM_SHA  "8c592ce4cfae5e0748314da56d73334de7465776")

FetchContent_Declare(imod_upstream
  GIT_REPOSITORY "${LIBIIMOD_UPSTREAM_GIT}"
  GIT_TAG        "${LIBIIMOD_UPSTREAM_SHA}"
  GIT_SHALLOW    FALSE
  GIT_PROGRESS   TRUE
)
FetchContent_MakeAvailable(imod_upstream)

set(IMOD_SRC "${imod_upstream_SOURCE_DIR}")

# ── Source / header lists ────────────────────────────────────────
set(LIBIIMOD_SOURCES
  "${IMOD_SRC}/libiimod/iilikemrc.c"
  "${IMOD_SRC}/libiimod/iimage.c"
  "${IMOD_SRC}/libiimod/iimrc.c"
  "${IMOD_SRC}/libiimod/iitif.c"
  "${IMOD_SRC}/libiimod/mrcfiles.c"
  "${IMOD_SRC}/libiimod/mrcsec.c"
  "${IMOD_SRC}/libiimod/mrcslice.c"
  "${IMOD_SRC}/libiimod/plist.c"
  "${IMOD_SRC}/libiimod/sliceproc.c"
  "${IMOD_SRC}/libiimod/diffusion.c"
  "${IMOD_SRC}/libcfshr/b3dutil.c"
  "${IMOD_SRC}/libcfshr/ilist.c"
  "${IMOD_SRC}/libcfshr/islice.c"
  "${IMOD_SRC}/libcfshr/parallelwrite.c"
)

set(LIBIIMOD_HEADERS
  "${IMOD_SRC}/include/b3dutil.h"
  "${IMOD_SRC}/include/cfsemshare.h"
  "${IMOD_SRC}/include/hvemtypes.h"
  "${IMOD_SRC}/include/iimage.h"
  "${IMOD_SRC}/include/ilist.h"
  "${IMOD_SRC}/include/mrcc.h"
  "${IMOD_SRC}/include/mrcfiles.h"
  "${IMOD_SRC}/include/mrcslice.h"
  "${IMOD_SRC}/include/sliceproc.h"
)

foreach(_f IN LISTS LIBIIMOD_SOURCES LIBIIMOD_HEADERS)
  if(NOT EXISTS "${_f}")
    message(FATAL_ERROR
      "libiimod: expected upstream file not found: ${_f}\n"
      "Pinned commit ${LIBIIMOD_UPSTREAM_SHA} layout may have drifted."
    )
  endif()
endforeach()

# ── imodconfig.h ─────────────────────────────────────────────────
# Endianness override for non-x86 hosts. The shipped imodconfig.h
# defaults to B3D_LITTLE_ENDIAN; if the build host is big-endian
# we add B3D_BIG_ENDIAN as a public compile definition and let the
# upstream sources branch accordingly.
test_big_endian(LIBIIMOD_BIG_ENDIAN)

# ── Library target ───────────────────────────────────────────────
add_library(iimod STATIC ${LIBIIMOD_SOURCES})
add_library(libiimod::iimod ALIAS iimod)

set_target_properties(iimod PROPERTIES
  OUTPUT_NAME             iimod
  POSITION_INDEPENDENT_CODE ON
  C_STANDARD              99
)

target_include_directories(iimod
  PUBLIC
    $<BUILD_INTERFACE:${IMOD_SRC}/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:include/libiimod>
)

target_compile_definitions(iimod
  PUBLIC
    $<$<BOOL:${LIBIIMOD_BIG_ENDIAN}>:B3D_BIG_ENDIAN>
)

# Link libtiff into libiimod. PUBLIC so consumers transitively pick
# up TIFF::TIFF when they link `libiimod::iimod` — callers of
# iiTIFFopen etc. never need to know libtiff is in play.
target_link_libraries(iimod PUBLIC TIFF::TIFF)

if(MSVC)
  target_compile_definitions(iimod PRIVATE _CRT_SECURE_NO_WARNINGS)
  target_compile_options(iimod PRIVATE /wd4244 /wd4267 /wd4996)
else()
  target_compile_options(iimod PRIVATE
    -Wno-implicit-function-declaration
    -Wno-incompatible-pointer-types
    -Wno-int-conversion
    -Wno-unused-but-set-variable
    -Wno-unused-variable
    -Wno-unused-function
    -Wno-parentheses
    -Wno-pointer-sign
    -Wno-format
  )
endif()

# ── Install ──────────────────────────────────────────────────────
install(TARGETS iimod
  EXPORT  libiimodTargets
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
)

install(FILES ${LIBIIMOD_HEADERS}
              "${CMAKE_CURRENT_SOURCE_DIR}/imodconfig.h"
        DESTINATION include/libiimod
)

install(EXPORT libiimodTargets
  FILE        libiimod-targets.cmake
  NAMESPACE   libiimod::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/libiimod
)

configure_package_config_file(
  "${CMAKE_CURRENT_SOURCE_DIR}/libiimod-config.cmake.in"
  "${CMAKE_CURRENT_BINARY_DIR}/libiimod-config.cmake"
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/libiimod
)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/libiimod-config-version.cmake"
  VERSION       ${PROJECT_VERSION}
  COMPATIBILITY SameMajorVersion
)

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/libiimod-config.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/libiimod-config-version.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/libiimod
)
