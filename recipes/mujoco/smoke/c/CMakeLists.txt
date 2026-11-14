# A C-only find_package(mujoco) consumer (native only; the wasm libmujoco.a
# needs a C++ link).  MuJoCo's API is C, and the package's C++17 requirement
# (cxx_std_17 on mujoco::mujoco) must not break a project that enables no C++.
cmake_minimum_required(VERSION 3.16)
project(mujoco_smoke_c C)

find_package(mujoco REQUIRED)

add_executable(mujoco_smoke_c main.c)
target_link_libraries(mujoco_smoke_c PRIVATE mujoco::mujoco)
