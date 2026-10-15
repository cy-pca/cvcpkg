# recipes/libigl/smoke — a minimal find_package(libigl) consumer, built and run by
# test.sh (native POSIX, wasm under node) and by build.ps1 on a Windows host.
cmake_minimum_required(VERSION 3.16)
project(igl_smoke CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Versioned on purpose: proves the recipe's libigl-config-version.cmake is paired
# with libigl-config.cmake (upstream's LibiglConfigVersion.cmake never is) and
# keeps SameMajorVersion semantics.
find_package(libigl 3 CONFIG QUIET)
if(libigl_FOUND)
  message(FATAL_ERROR "libigl ${libigl_VERSION} accepted a request for version 3")
endif()
find_package(libigl 2.6 CONFIG REQUIRED)   # find_dependency(Eigen3 CONFIG) + Threads inside

get_target_property(_igl_type igl::core TYPE)
if(NOT _igl_type STREQUAL "INTERFACE_LIBRARY")
  message(FATAL_ERROR "igl::core is ${_igl_type}, expected the header-only INTERFACE_LIBRARY")
endif()
get_target_property(_igl_defs igl::core INTERFACE_COMPILE_DEFINITIONS)
if(NOT _igl_defs)
  set(_igl_defs "(none)")
endif()
message(STATUS "igl::core ${libigl_VERSION} from ${libigl_DIR}; definitions: ${_igl_defs}")

add_executable(igl_smoke main.cpp)
target_link_libraries(igl_smoke PRIVATE igl::core)

if(EMSCRIPTEN)
  # EXIT_RUNTIME so node gets main()'s return code.
  target_link_options(igl_smoke PRIVATE
    -sSTACK_SIZE=1048576
    -sALLOW_MEMORY_GROWTH=1
    -sEXIT_RUNTIME=1)
endif()
