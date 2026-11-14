cmake_minimum_required(VERSION 3.16)
project(mujoco_smoke CXX)

# No CMAKE_CXX_STANDARD here: mujoco::mujoco must carry the C++17 its headers
# need from C++ (mjspec.h uses std::byte).  MSVC compiles C++14 by default, so
# on Windows this consumer only builds if the package requires cxx_std_17;
# the check below fails on every platform if it stops doing so.
find_package(mujoco REQUIRED)
get_target_property(_mujoco_features mujoco::mujoco INTERFACE_COMPILE_FEATURES)
if(NOT "cxx_std_17" IN_LIST _mujoco_features)
  message(FATAL_ERROR "mujoco::mujoco does not require cxx_std_17 "
    "(INTERFACE_COMPILE_FEATURES: ${_mujoco_features})")
endif()

add_executable(mujoco_smoke main.cc)
target_link_libraries(mujoco_smoke PRIVATE mujoco::mujoco)
if(EMSCRIPTEN)
  # libmujoco is built with exceptions on; memory growth for model buffers.
  target_link_options(mujoco_smoke PRIVATE -fexceptions -sALLOW_MEMORY_GROWTH=1)
endif()
