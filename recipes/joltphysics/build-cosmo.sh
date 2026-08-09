#!/usr/bin/env bash
# recipes/joltphysics/build-cosmo.sh — cross-compile Jolt Physics with Cosmopolitan.
#
# Jolt is headless pure computation, and cosmocc is an ordinary x86-64 C++
# toolchain over a POSIX libc, so the two fit without adaptation:
#
#   * Platform detection.  Jolt selects its platform from the usual feature
#     macros; cosmocc defines __linux__ (the same property recipes/abseil relies
#     on for its cosmo build), so Jolt takes its Linux branch.
#   * Threads.  Jolt's core compiles JobSystemThreadPool and Semaphore, which
#     need std::thread, std::mutex and std::condition_variable.  Cosmopolitan
#     implements pthreads and its C++ runtime exposes those — recipes/log4cplus,
#     which is thread-driven C++, already builds at this target.
#   * Static-only / no dlopen is irrelevant: Jolt is linked in, not loaded.
#
# ISA baseline: the x86 SIMD switches are OFF, so Jolt uses its scalar Vec4/Mat44
# path rather than the SSE4.2 baseline the native x86-64 build takes.  That is
# deliberate and matches what recipes/libpng does on cosmo — an APE is meant to
# be the artefact that runs anywhere, and SSE4.2 silently narrows it to
# Nehalem-and-later hosts.  Jolt's own SSE2 floor would be safe, but the switches
# it exposes are 4.1/4.2 and above, so OFF is the portable setting.
#
# ABI note: this changes Jolt's ABI relative to the native build, which is safe
# only because Jolt publishes its JPH_* settings as PUBLIC compile definitions on
# the exported Jolt::Jolt target — a consumer linking that target inherits the
# matching configuration.  Consumers that hand-roll their own flags instead will
# mismatch; that caveat is already documented in recipe.yaml.
#
# ENABLE_ALL_WARNINGS=OFF must stay: it turns on -Werror, and cvc.2 of this
# recipe exists because upstream's -Werror failed on a compiler upstream had not
# tested.  cosmocc's GCC is another such compiler.
#
# CMAKE_CXX_STANDARD=17 is passed explicitly because env-cosmo.sh's
# cvc_cmake_build — unlike the wasm and wasi ones — does not set it, and Jolt
# requires C++17.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

# Jolt's CMakeLists.txt lives in Build/, not at the tarball root.
CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/Build"

cvc_cmake_build \
    -DCMAKE_CXX_STANDARD=17 \
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
