# recipes/wasmtime/smoke: a find_package(wasmtime) consumer that test.sh builds against the
# bundle. It goes through the generated wasmtime::wasmtime target, which is the point: the target's
# type decides what the consumer records as the library it needs.
cmake_minimum_required(VERSION 3.16)
project(wasmtime_smoke C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

find_package(wasmtime CONFIG REQUIRED)

add_executable(wasmtime_smoke main.c)
target_link_libraries(wasmtime_smoke PRIVATE wasmtime::wasmtime)

# Linked with its installed run path only, so test.sh can copy it into a bin/ + lib/ layout away
# from the prefix and run it there.
if(APPLE)
  set(_rpath "@loader_path/../lib")
else()
  set(_rpath "$ORIGIN/../lib")
endif()
set_target_properties(wasmtime_smoke PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE
                                                INSTALL_RPATH "${_rpath}")

get_target_property(_type wasmtime::wasmtime TYPE)
file(WRITE "${CMAKE_BINARY_DIR}/wasmtime_target_type.txt" "${_type}")
