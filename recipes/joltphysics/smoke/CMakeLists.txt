# recipes/joltphysics/smoke — a minimal find_package(Jolt) consumer used by
# test.sh on every platform (native + wasm).  Going through the exported
# Jolt::Jolt target is the point: it carries the PUBLIC JPH_* definitions, and
# JPH::RegisterTypes() std::abort()s (even without JPH_ENABLE_ASSERTS) when the
# consumer's JPH_VERSION_ID disagrees with the library's — so a green run proves
# the ABI contract the recipe description promises, not just that a .a exists.
cmake_minimum_required(VERSION 3.16)
project(jolt_smoke CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Jolt CONFIG REQUIRED)

add_executable(jolt_smoke main.cpp)
target_link_libraries(jolt_smoke PRIVATE Jolt::Jolt)

if(EMSCRIPTEN)
  # Jolt's own guidance for wasm executables (Build/CMakeLists.txt, HelloWorld):
  # more than the 64 KB default stack and 16 MB initial heap.  EXIT_RUNTIME so
  # node gets main()'s return code.  No -pthread: the catalog bundle is the
  # single-threaded build and the program uses JobSystemSingleThreaded.
  target_link_options(jolt_smoke PRIVATE
    -sSTACK_SIZE=1048576
    -sINITIAL_MEMORY=134217728
    -sALLOW_MEMORY_GROWTH=1
    -sEXIT_RUNTIME=1)
endif()
