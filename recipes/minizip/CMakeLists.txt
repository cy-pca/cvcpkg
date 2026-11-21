# recipes/minizip/CMakeLists.txt -- classic minizip (zlib's contrib/minizip), which upstream ships
# with Makefiles and autotools only. Same headers (<minizip/unzip.h>, <minizip/zip.h>, ...), CMake
# package (unofficial-minizip) and target (unofficial::minizip::minizip) as vcpkg's minizip port,
# with bzip2 support on, so projects written against that port build unchanged.
cmake_minimum_required(VERSION 3.21)
project(unofficial-minizip C)

if (NOT ZLIB_SOURCE_DIR)
  message(FATAL_ERROR "ZLIB_SOURCE_DIR must point at an unpacked zlib source tree")
endif ()
# Forward slashes: a Windows path's backslashes would be read as escapes in the source list.
file(TO_CMAKE_PATH "${ZLIB_SOURCE_DIR}" zlib_src)
set(src "${zlib_src}/contrib/minizip")

find_package(ZLIB REQUIRED)
find_package(BZip2 REQUIRED)

add_library(minizip
  "${src}/ioapi.c"
  "${src}/unzip.c"
  "${src}/zip.c"
  "${src}/mztools.c"
  "$<$<PLATFORM_ID:Windows>:${src}/iowin32.c>"
)
target_compile_definitions(minizip PRIVATE HAVE_BZIP2 _CRT_SECURE_NO_WARNINGS _CRT_NONSTDC_NO_DEPRECATE)
target_include_directories(minizip
  PRIVATE "${src}"
  INTERFACE "$<INSTALL_INTERFACE:include>")
target_link_libraries(minizip PRIVATE ZLIB::ZLIB BZip2::BZip2)
# minizip's sources carry no dllexport annotations; every symbol it has is C API.
set_target_properties(minizip PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)

install(TARGETS minizip EXPORT unofficial-minizip-targets
  RUNTIME DESTINATION bin LIBRARY DESTINATION lib ARCHIVE DESTINATION lib)
install(FILES
    "${src}/ioapi.h" "${src}/unzip.h" "${src}/zip.h" "${src}/mztools.h" "${src}/iowin32.h"
    "${src}/crypt.h"
  DESTINATION include/minizip)
install(EXPORT unofficial-minizip-targets
  NAMESPACE unofficial::minizip::
  DESTINATION share/unofficial-minizip)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/unofficial-minizipConfig.cmake" [=[
include(CMakeFindDependencyMacro)
find_dependency(ZLIB)
find_dependency(BZip2)
include("${CMAKE_CURRENT_LIST_DIR}/unofficial-minizip-targets.cmake")
]=])
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/unofficial-minizipConfig.cmake"
  DESTINATION share/unofficial-minizip)
