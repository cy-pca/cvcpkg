# recipes/libsodium/msvc/CMakeLists.txt -- libsodium with MSVC, as upstream's
# builds/msvc/vs2022/libsodium.vcxproj builds it, for a Windows host whose
# compiler has no MSBuild (cvcpkg's own MSVC: recipes/msvc).
#
# build.ps1 copies this file to the root of the extracted source and runs it
# through Invoke-CvcCMakeBuild.  What it reproduces from the vs2022 project
# (libsodium.props and properties/*.props, 1.0.22):
#   - every .c under src/libsodium (the project lists all 141 of them);
#   - include dirs src/libsodium/include and src/libsodium/include/sodium;
#   - defines inline=__inline, NATIVE_LITTLE_ENDIAN, _CRT_SECURE_NO_WARNINGS,
#     plus SODIUM_DLL_EXPORT for the DLL or SODIUM_STATIC for the library;
#   - include/sodium/version.h from builds/msvc/version.h, which the project's
#     pre-build step copies in.  It goes into the build tree here, so the
#     source tree stays as extracted;
#   - the DLL's version resource (builds/msvc/resource.rc).
# MSVC picks libsodium's SSE/AVX kernels itself (private/common.h names the
# intrinsics headers under _MSC_VER), and the runtime CPU dispatch chooses
# among them, so no per-file ISA flags are needed.
cmake_minimum_required(VERSION 3.16)
project(libsodium VERSION 1.0.22 LANGUAGES C)

if(NOT MSVC)
  message(FATAL_ERROR "recipes/libsodium/msvc/CMakeLists.txt is for MSVC; "
                      "other platforms build with upstream's ./configure")
endif()

set(SODIUM_SRC "${CMAKE_CURRENT_SOURCE_DIR}/src/libsodium")
file(GLOB_RECURSE SODIUM_SOURCES CONFIGURE_DEPENDS "${SODIUM_SRC}/*.c")
list(LENGTH SODIUM_SOURCES _n)
if(NOT _n EQUAL 141)
  message(FATAL_ERROR "libsodium: ${_n} sources under src/libsodium, the vs2022 "
                      "project builds 141; compare it before building")
endif()

set(SODIUM_GEN_INCLUDE "${CMAKE_CURRENT_BINARY_DIR}/include")
configure_file("${CMAKE_CURRENT_SOURCE_DIR}/builds/msvc/version.h"
               "${SODIUM_GEN_INCLUDE}/sodium/version.h" COPYONLY)

if(BUILD_SHARED_LIBS)
  add_library(sodium SHARED ${SODIUM_SOURCES} "${CMAKE_CURRENT_SOURCE_DIR}/builds/msvc/resource.rc")
  target_compile_definitions(sodium PRIVATE SODIUM_DLL_EXPORT)
else()
  add_library(sodium STATIC ${SODIUM_SOURCES})
  target_compile_definitions(sodium PUBLIC SODIUM_STATIC=1)
endif()
# Upstream's file names: libsodium.dll, and libsodium.lib (its import library,
# or the static library).
set_target_properties(sodium PROPERTIES OUTPUT_NAME libsodium)
target_compile_definitions(sodium PRIVATE inline=__inline NATIVE_LITTLE_ENDIAN
                                          _CRT_SECURE_NO_WARNINGS)
# The generated include dir first, so sodium/version.h is the MSVC one.
target_include_directories(sodium
  PRIVATE "${SODIUM_GEN_INCLUDE}" "${SODIUM_GEN_INCLUDE}/sodium"
          "${SODIUM_SRC}/include" "${SODIUM_SRC}/include/sodium"
  INTERFACE "$<INSTALL_INTERFACE:include>")
# The vs2022 project's warning settings (Common.props /W3, libsodium.props
# 4146 and 4244 off).
target_compile_options(sodium PRIVATE /W3 /wd4146 /wd4244)

include(GNUInstallDirs)
install(TARGETS sodium EXPORT sodiumTargets
  RUNTIME DESTINATION bin
  LIBRARY DESTINATION lib
  ARCHIVE DESTINATION lib)
# Public headers only: sodium.h and sodium/*.h, not sodium/private/.
install(FILES "${SODIUM_SRC}/include/sodium.h" DESTINATION include)
install(DIRECTORY "${SODIUM_SRC}/include/sodium/" DESTINATION include/sodium
  FILES_MATCHING PATTERN "*.h" PATTERN "private" EXCLUDE)
install(FILES "${SODIUM_GEN_INCLUDE}/sodium/version.h" DESTINATION include/sodium)

install(EXPORT sodiumTargets NAMESPACE sodium:: DESTINATION lib/cmake/sodium)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/sodiumConfig.cmake"
  "# sodiumConfig.cmake -- written by cvcpkg's libsodium recipe (libsodium ${PROJECT_VERSION}).\n"
  "include(\"\${CMAKE_CURRENT_LIST_DIR}/sodiumTargets.cmake\")\n"
  "set(sodium_FOUND TRUE)\n")
include(CMakePackageConfigHelpers)
write_basic_package_version_file("${CMAKE_CURRENT_BINARY_DIR}/sodiumConfigVersion.cmake"
  VERSION ${PROJECT_VERSION} COMPATIBILITY SameMajorVersion)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/sodiumConfig.cmake"
              "${CMAKE_CURRENT_BINARY_DIR}/sodiumConfigVersion.cmake"
        DESTINATION lib/cmake/sodium)

# pkg-config, as upstream's libsodium.pc.in writes it (static: the define too).
if(BUILD_SHARED_LIBS)
  set(_pc_cflags "-I\${includedir}")
else()
  set(_pc_cflags "-I\${includedir} -DSODIUM_STATIC=1")
endif()
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/libsodium.pc"
  "prefix=\${pcfiledir}/../..\n"
  "exec_prefix=\${prefix}\n"
  "libdir=\${exec_prefix}/lib\n"
  "includedir=\${prefix}/include\n"
  "\n"
  "Name: libsodium\n"
  "Version: ${PROJECT_VERSION}\n"
  "Description: A modern and easy-to-use crypto library\n"
  "\n"
  "Libs: -L\${libdir} -llibsodium\n"
  "Cflags: ${_pc_cflags}\n")
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/libsodium.pc" DESTINATION lib/pkgconfig)
