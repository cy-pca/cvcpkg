cmake_minimum_required(VERSION 3.16)
project(mujoco_smoke CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(mujoco REQUIRED)

add_executable(mujoco_smoke main.cc)
target_link_libraries(mujoco_smoke PRIVATE mujoco::mujoco)
if(EMSCRIPTEN)
  # libmujoco is built with exceptions on; memory growth for model buffers.
  target_link_options(mujoco_smoke PRIVATE -fexceptions -sALLOW_MEMORY_GROWTH=1)
endif()
