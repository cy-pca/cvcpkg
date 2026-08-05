#!/usr/bin/env bash
# recipes/joltphysics/build-wasm.sh — cross-compile Jolt Physics to wasm via Emscripten.
#
# Jolt is a headless rigid-body solver — collision detection and constrained
# dynamics over its own arena allocators.  It opens no files, no sockets and no
# display, and upstream supports Emscripten directly (Jolt defines
# JPH_PLATFORM_WASM for __EMSCRIPTEN__ and ships a wasm build script; the
# published jolt-physics JS package is an Emscripten build of this source).
#
# Two structural notes:
#
#   * Jolt's CMake project root is the Build/ subdirectory, not the tarball
#     root, so CVC_SOURCE_DIR is repointed before calling the helper — the same
#     pattern recipes/zstd and recipes/lz4 use for their nested CMakeLists.
#     Going through cvc_cmake_build (rather than the raw `cmake` invocation the
#     native build.sh needs) is what injects the Emscripten toolchain file.
#
#   * Every x86 ISA switch is forced OFF.  Emscripten's toolchain file reports
#     CMAKE_SYSTEM_PROCESSOR as "x86", so Jolt would otherwise select its SSE
#     baseline and pass -msse4.2 to a compiler targeting a different instruction
#     set entirely.  With these off Jolt falls back to the scalar path in its
#     Vec4/Mat44 layer, which is the portable configuration.
#
# ENABLE_ALL_WARNINGS=OFF is not cosmetic and must stay: it enables -Werror, and
# cvc.2 of this recipe exists precisely because upstream's -Werror broke the
# build on a compiler upstream had not tested (see recipe.yaml).  A new compiler
# is exactly what a cross target introduces.
#
# FLOATING_POINT_EXCEPTIONS_ENABLED=OFF matters more here than natively: it
# installs SIGFPE-style FP traps, which wasm has no equivalent for.
#
# ABI note: Jolt attaches its JPH_* settings to the exported Jolt::Jolt target as
# PUBLIC compile definitions, so a consumer that links the target inherits this
# no-SIMD configuration automatically and stays ABI-compatible with it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# Jolt's CMakeLists.txt lives in Build/, not at the tarball root.
CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/Build"

cvc_cmake_build \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DJPH_BUILD_SHARED_LIBS=OFF \
    -DENABLE_INSTALL=ON \
    -DTARGET_UNIT_TESTS=OFF \
    -DTARGET_HELLO_WORLD=OFF \
    -DTARGET_PERFORMANCE_TEST=OFF \
    -DTARGET_SAMPLES=OFF \
    -DTARGET_VIEWER=OFF \
    -DOVERRIDE_CXX_FLAGS=OFF \
    -DGENERATE_DEBUG_SYMBOLS=OFF \
    -DFLOATING_POINT_EXCEPTIONS_ENABLED=OFF \
    -DINTERPROCEDURAL_OPTIMIZATION=OFF \
    -DENABLE_ALL_WARNINGS=OFF \
    -DJPH_USE_DX12=OFF \
    -DJPH_USE_VK=OFF \
    -DJPH_USE_MTL=OFF \
    -DUSE_SSE4_1=OFF \
    -DUSE_SSE4_2=OFF \
    -DUSE_AVX=OFF \
    -DUSE_AVX2=OFF \
    -DUSE_AVX512=OFF \
    -DUSE_LZCNT=OFF \
    -DUSE_TZCNT=OFF \
    -DUSE_F16C=OFF \
    -DUSE_FMADD=OFF
