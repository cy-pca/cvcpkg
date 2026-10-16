# recipes/joltphysics/smoke — a minimal find_package(Jolt) consumer used by
# test.sh on every platform (native, wasm and wasm-mt).  Going through the
# exported Jolt::Jolt target is the point: it carries the PUBLIC JPH_*
# definitions, and JPH::RegisterTypes() std::abort()s (even without
# JPH_ENABLE_ASSERTS) when the consumer's JPH_VERSION_ID disagrees with the
# library's, so a green run proves the ABI contract the recipe description
# promises, not just that a .a exists.
cmake_minimum_required(VERSION 3.16)
project(jolt_smoke CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Run the scene a second time on a JobSystemThreadPool and require bit-identical
# results. On by default natively; under Emscripten only for the threaded
# (wasm-mt) bundle, which test.sh selects with -DJOLT_SMOKE_THREADS=ON.
if(EMSCRIPTEN)
  option(JOLT_SMOKE_THREADS "exercise JobSystemThreadPool" OFF)
else()
  option(JOLT_SMOKE_THREADS "exercise JobSystemThreadPool" ON)
endif()

find_package(Jolt CONFIG REQUIRED)

add_executable(jolt_smoke main.cpp)
target_link_libraries(jolt_smoke PRIVATE Jolt::Jolt)
if(JOLT_SMOKE_THREADS)
  target_compile_definitions(jolt_smoke PRIVATE JOLT_SMOKE_THREADS)
  if(NOT EMSCRIPTEN)
    find_package(Threads REQUIRED)
    target_link_libraries(jolt_smoke PRIVATE Threads::Threads)
  endif()
endif()

if(EMSCRIPTEN)
  # Jolt's own guidance for wasm executables (Build/CMakeLists.txt, HelloWorld):
  # more than the 64 KB default stack and 16 MB initial heap.  EXIT_RUNTIME so
  # node gets main()'s return code.
  target_link_options(jolt_smoke PRIVATE
    -sSTACK_SIZE=1048576
    -sINITIAL_MEMORY=134217728
    -sALLOW_MEMORY_GROWTH=1
    -sEXIT_RUNTIME=1)
  if(JOLT_SMOKE_THREADS)
    # wasm-mt: -pthread on every object (wasm-ld refuses to mix), and the three
    # pool workers pre-spawned, because a pthread_create from main() cannot wait
    # for a worker to start without returning to the event loop.
    target_compile_options(jolt_smoke PRIVATE -pthread)
    target_link_options(jolt_smoke PRIVATE -pthread -sPTHREAD_POOL_SIZE=4)
  endif()
endif()
