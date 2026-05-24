# levmar — Levenberg-Marquardt nonlinear least-squares (M. Lourakis),
# built from the upstream 2.6 tarball
#   https://users.ics.forth.gr/~lourakis/levmar/levmar-2.6.tgz
# (last released November 2011 — stable; SHA256
#  3bf4ef1ea4475ded5315e8d8fc992a725f2e7940a74ca3b0f9029d9e6e94bad7).
#
# The sources are VENDORED in ./upstream/ (rather than fetched at
# configure time) because the host users.ics.forth.gr serves the
# tarball over an HTTPS endpoint whose certificate chain regularly
# fails to validate on GitHub-hosted runners, which breaks CI
# unreliably. Vendoring is also how libcvc historically shipped
# levmar-2.5.
#
# What is vendored (under ./upstream/):
#   lm.c lm_core.c lm.h
#   Axb.c Axb_core.c
#   misc.c misc_core.c misc.h
#   lmlec.c lmlec_core.c
#   lmbc.c lmbc_core.c
#   lmblec.c lmblec_core.c
#   lmbleic.c lmbleic_core.c
#   compiler.h
#   levmar.h.in       (configured by us with HAVE_LAPACK defined)
#   LICENSE README.txt
#
# Only the 7 top-level .c files are compiled; the _core.c files are
# included twice (once for double, once for single precision) from
# their respective top-level wrapper. Demo/Matlab sources (lmdemo.c,
# expfit.c, matlab/) are NOT shipped.
#
# LAPACK is mandatory. libcvc-deps already provides LAPACK on all
# three platforms (Linux: liblapack-dev/libblas-dev; macOS: brew
# lapack / Apple Accelerate; Windows: vcpkg clapack + openblas), so
# wiring LAPACK in costs nothing and lights up the full levmar API
# surface — including the constrained variants (`*_bc_*`, `*_lec_*`,
# `*_blec_*`, `*_bleic_*`) that the LAPACK-free fallback build
# cannot offer. This matches TexMol's existing vendored levmar-2.5
# which also defines HAVE_LAPACK.
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

include(CMakePackageConfigHelpers)
include(GNUInstallDirs)

# LAPACK is mandatory; see header comment.
#
# On Windows we prefer vcpkg's `clapack` port directly via its
# CONFIG package, because:
#   * vcpkg's openblas on MSVC ships only BLAS (LAPACK requires
#     Fortran, which isn't available in the MSVC build), so the
#     stock FindLAPACK module fails to locate `cheev_`.
#   * vcpkg's `clapack` port DOES install a vcpkg-cmake-wrapper
#     redirecting find_package(LAPACK), but that wrapper was only
#     added in mid-2026 and runners with an older bundled vcpkg
#     baseline don't have it — so we cannot rely on
#     find_package(LAPACK) routing through clapack automatically.
#
# clapack's CONFIG package exports targets `lapack` (the LAPACK
# library, f2c'd) and `f2c` (the libf2c runtime); it also pulls in
# its BLAS backend (vcpkg's openblas) internally. We synthesise
# the `LAPACK::LAPACK` INTERFACE target ourselves so the link
# command below is platform-uniform.
set(_levmar_have_lapack FALSE)
if(WIN32)
  find_package(clapack CONFIG QUIET)
  if(clapack_FOUND AND TARGET lapack)
    if(NOT TARGET LAPACK::LAPACK)
      add_library(LAPACK::LAPACK INTERFACE IMPORTED)
      set(_levmar_lapack_link lapack)
      if(TARGET f2c)
        list(APPEND _levmar_lapack_link f2c)
      endif()
      set_property(TARGET LAPACK::LAPACK PROPERTY
        INTERFACE_LINK_LIBRARIES "${_levmar_lapack_link}")
    endif()
    set(_levmar_have_lapack TRUE)
    message(STATUS "levmar: using vcpkg clapack CONFIG for LAPACK")
  endif()
endif()
if(NOT _levmar_have_lapack)
  # On Linux with static OpenBLAS the stock FindBLAS module fails because
  # the link-test for sgemm_ cannot resolve gfortran runtime symbols.
  # OpenBLAS's own CMake config handles transitive deps correctly and
  # provides both BLAS and LAPACK, so prefer it when available.
  find_package(OpenBLAS CONFIG QUIET)
  if(OpenBLAS_FOUND AND TARGET OpenBLAS::OpenBLAS)
    if(NOT TARGET LAPACK::LAPACK)
      add_library(LAPACK::LAPACK INTERFACE IMPORTED)
      set_property(TARGET LAPACK::LAPACK PROPERTY
        INTERFACE_LINK_LIBRARIES OpenBLAS::OpenBLAS)
    endif()
    set(_levmar_have_lapack TRUE)
    message(STATUS "levmar: using OpenBLAS CONFIG for LAPACK")
  endif()
endif()
if(NOT _levmar_have_lapack)
  find_package(LAPACK REQUIRED)
endif()

set(LEVMAR_SRC "${CMAKE_CURRENT_SOURCE_DIR}/upstream")

# ── Source / header lists ────────────────────────────────────────
# Full library source set (unconstrained + constrained variants).
# Only the top-level .c files are passed to the compiler; each
# #includes its _core.c sibling twice (LM_DBL_PREC / LM_SNGL_PREC).
set(LEVMAR_SOURCES
  "${LEVMAR_SRC}/lm.c"
  "${LEVMAR_SRC}/Axb.c"
  "${LEVMAR_SRC}/misc.c"
  "${LEVMAR_SRC}/lmlec.c"
  "${LEVMAR_SRC}/lmbc.c"
  "${LEVMAR_SRC}/lmblec.c"
  "${LEVMAR_SRC}/lmbleic.c"
)

# Files that must also be present (transitive _core.c includes +
# private headers) — listed for the existence sanity-check below.
set(LEVMAR_AUX_FILES
  "${LEVMAR_SRC}/lm_core.c"
  "${LEVMAR_SRC}/Axb_core.c"
  "${LEVMAR_SRC}/misc_core.c"
  "${LEVMAR_SRC}/lmlec_core.c"
  "${LEVMAR_SRC}/lmbc_core.c"
  "${LEVMAR_SRC}/lmblec_core.c"
  "${LEVMAR_SRC}/lmbleic_core.c"
  "${LEVMAR_SRC}/misc.h"
  "${LEVMAR_SRC}/compiler.h"
  "${LEVMAR_SRC}/lm.h"
  "${LEVMAR_SRC}/levmar.h.in"
)

foreach(_f IN LISTS LEVMAR_SOURCES LEVMAR_AUX_FILES)
  if(NOT EXISTS "${_f}")
    message(FATAL_ERROR
      "levmar: expected vendored file not found: ${_f}\n"
      "third-party/levmar/upstream/ may be missing or incomplete.")
  endif()
endforeach()

# ── Configure levmar.h (HAVE_LAPACK enabled) ────────────────────
# Upstream's levmar.h.in has `#cmakedefine` tokens for HAVE_LAPACK,
# HAVE_PLASMA, LINSOLVERS_RETAIN_MEMORY, LM_DBL_PREC and LM_SNGL_PREC.
# We enable LAPACK and BOTH precisions (matching the unconditional
# code paths in the .c files which compile both flavours).
# The tarball also ships a pre-generated `levmar.h` next to
# `levmar.h.in`, but we deliberately do NOT vendor that file —
# our configured copy is the authoritative one.
set(HAVE_LAPACK 1)
set(LM_DBL_PREC 1)
set(LM_SNGL_PREC 1)
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
# path for the build, and the configured levmar.h must come first.
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
