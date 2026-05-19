# levmar — Levenberg-Marquardt nonlinear least-squares (M. Lourakis),
# built from the official upstream tarball at
#   https://users.ics.forth.gr/~lourakis/levmar/levmar-2.6.tgz
# (last released November 2011 — stable).
#
# Upstream ships a tiny CMakeLists.txt with no install rules and a
# loose LAPACK wire-up. We replace it: compile the full source set,
# discover LAPACK via the standard CMake module, ship a configured
# `levmar.h` with HAVE_LAPACK defined, and install a CMake config so
# consumers can do `find_package(levmar CONFIG REQUIRED)` and link
# `levmar::levmar`.
#
# LAPACK is mandatory: libcvc-deps already bundles LAPACK + BLAS on
# all three platforms (Linux: liblapack-dev/libblas-dev; macOS:
# brew lapack / Apple Accelerate; Windows: vcpkg clapack + openblas),
# so wiring LAPACK in costs nothing and lights up the full levmar
# API surface — including the constrained variants (`*_bc_*`,
# `*_lec_*`, `*_blec_*`, `*_bleic_*`) that the LAPACK-free fallback
# build cannot offer.
#
# This matches the behaviour of TexMol's existing vendored
# levmar-2.5, which also defines HAVE_LAPACK and links against
# system LAPACK.
#
# Output:
#   ${CMAKE_INSTALL_LIBDIR}/liblevmar.{a,lib}
#   include/levmar/levmar.h
#   ${CMAKE_INSTALL_LIBDIR}/cmake/levmar/levmar-config.cmake
#   ${CMAKE_INSTALL_LIBDIR}/cmake/levmar/levmar-targets.cmake

cmake_minimum_required(VERSION 3.16)
project(levmar
  VERSION 2.6.0
  DESCRIPTION "Levenberg-Marquardt nonlinear least-squares (Lourakis)"
  LANGUAGES C
)

include(FetchContent)
include(CMakePackageConfigHelpers)
include(GNUInstallDirs)

# LAPACK is mandatory; see header comment.
find_package(LAPACK REQUIRED)

# Pinned upstream tarball. SHA256 captured 2026-05-18 from
# https://users.ics.forth.gr/~lourakis/levmar/levmar-2.6.tgz
set(LEVMAR_UPSTREAM_URL
  "https://users.ics.forth.gr/~lourakis/levmar/levmar-2.6.tgz")
set(LEVMAR_UPSTREAM_SHA256
  "3bf4ef1ea4475ded5315e8d8fc992a725f2e7940a74ca3b0f9029d9e6e94bad7")

FetchContent_Declare(levmar_upstream
  URL                        "${LEVMAR_UPSTREAM_URL}"
  URL_HASH                   "SHA256=${LEVMAR_UPSTREAM_SHA256}"
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
# Download + extract but do NOT add_subdirectory(): upstream's
# CMakeLists.txt defines a `levmar` target of its own (with its own
# LAPACK wire-up) which would conflict with ours.
FetchContent_GetProperties(levmar_upstream)
if(NOT levmar_upstream_POPULATED)
  FetchContent_Populate(levmar_upstream)
endif()

set(LEVMAR_SRC "${levmar_upstream_SOURCE_DIR}")

# ── Source / header lists ────────────────────────────────────────
# Full library source set (unconstrained + constrained variants).
set(LEVMAR_SOURCES
  "${LEVMAR_SRC}/lm.c"
  "${LEVMAR_SRC}/Axb.c"
  "${LEVMAR_SRC}/misc.c"
  "${LEVMAR_SRC}/lmlec.c"
  "${LEVMAR_SRC}/lmbc.c"
  "${LEVMAR_SRC}/lmblec.c"
  "${LEVMAR_SRC}/lmbleic.c"
)

set(LEVMAR_PRIVATE_HEADERS
  "${LEVMAR_SRC}/misc.h"
  "${LEVMAR_SRC}/compiler.h"
  "${LEVMAR_SRC}/lm.h"
)

foreach(_f IN LISTS LEVMAR_SOURCES LEVMAR_PRIVATE_HEADERS)
  if(NOT EXISTS "${_f}")
    message(FATAL_ERROR
      "levmar: expected upstream file not found: ${_f}\n"
      "Pinned tarball ${LEVMAR_UPSTREAM_URL} layout may have changed.")
  endif()
endforeach()

# ── Configure levmar.h (HAVE_LAPACK enabled) ────────────────────
# Upstream's levmar.h.in has a single `#cmakedefine HAVE_LAPACK`
# token; setting HAVE_LAPACK=1 here makes configure_file emit
# `#define HAVE_LAPACK` in the installed header. The tarball also
# ships a pre-generated `levmar.h` next to `levmar.h.in`; our
# configured copy shadows it via the include-dir ordering below.
set(HAVE_LAPACK 1)
configure_file(
  "${LEVMAR_SRC}/levmar.h.in"
  "${CMAKE_CURRENT_BINARY_DIR}/include/levmar/levmar.h"
  @ONLY
)

# ── Library target ───────────────────────────────────────────────
add_library(levmar STATIC ${LEVMAR_SOURCES})
add_library(levmar::levmar ALIAS levmar)

set_target_properties(levmar PROPERTIES
  OUTPUT_NAME              levmar
  POSITION_INDEPENDENT_CODE ON
  C_STANDARD               99
)

# Upstream sources `#include "levmar.h"` and "misc.h" / "compiler.h"
# unqualified, so the upstream source dir must be on the include
# path for the build, and the configured levmar.h must shadow the
# tarball-shipped one. Order matters: configured-include dir first.
target_include_directories(levmar
  PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/include/levmar"
    "${LEVMAR_SRC}"
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

# Public link to LAPACK so consumers of the static archive don't
# have to know to pull in LAPACK themselves.
target_link_libraries(levmar PUBLIC LAPACK::LAPACK)

if(MSVC)
  target_compile_definitions(levmar PRIVATE _CRT_SECURE_NO_WARNINGS)
  # levmar uses a fair amount of double<->float narrowing and old-C
  # idioms; silence the resulting noise rather than patch upstream.
  target_compile_options(levmar PRIVATE /wd4244 /wd4267 /wd4996 /wd4101)
else()
  target_compile_options(levmar PRIVATE
    -Wno-implicit-function-declaration
    -Wno-unused-but-set-variable
    -Wno-unused-variable
    -Wno-unused-function
    -Wno-parentheses
    -Wno-format
  )
  # levmar calls sqrt/sqrtf/fabs from libm. On Linux link libm
  # publicly so consumers of the static archive don't have to know
  # to add `-lm` themselves. macOS folds libm into libSystem.
  find_library(LIBM_LIBRARY m)
  if(LIBM_LIBRARY)
    target_link_libraries(levmar PUBLIC ${LIBM_LIBRARY})
  endif()
endif()

# ── Install ──────────────────────────────────────────────────────
install(TARGETS levmar
  EXPORT  levmarTargets
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
)

install(FILES "${CMAKE_CURRENT_BINARY_DIR}/include/levmar/levmar.h"
        DESTINATION include/levmar
)

install(EXPORT levmarTargets
  FILE        levmar-targets.cmake
  NAMESPACE   levmar::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/levmar
)

configure_package_config_file(
  "${CMAKE_CURRENT_SOURCE_DIR}/levmar-config.cmake.in"
  "${CMAKE_CURRENT_BINARY_DIR}/levmar-config.cmake"
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/levmar
)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/levmar-config-version.cmake"
  VERSION       ${PROJECT_VERSION}
  COMPATIBILITY SameMajorVersion
)

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/levmar-config.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/levmar-config-version.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/levmar
)
