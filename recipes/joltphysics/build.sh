#!/usr/bin/env bash
# recipes/joltphysics/build.sh — build Jolt Physics.
#
# Jolt's CMake project root is the Build/ subdirectory (not the repo root), and
# when Build/ IS the top-level source it would otherwise also build the unit
# tests, HelloWorld, performance test, samples and viewer (all default ON) —
# which pull in a test framework and GPU/rendering deps — so every TARGET_* app
# is turned off and only the core library is built.
#
# ENABLE_ALL_WARNINGS is turned OFF: it enables -Werror (and MSVC /WX), which
# gates OUR build on upstream's warning-cleanliness across every compiler in the
# fleet.  Jolt is warning-clean on its own tested compilers, but a stricter
# builder GCC trips a -Wuninitialized false positive in Jolt/Core/HashTable.h
# and fails the build; a distributed package must not be -Werror-gated that way.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# CVC_LINK -> Jolt's shared/static switch.  JPH_BUILD_SHARED_LIBS defaults to
# BUILD_SHARED_LIBS; set both explicitly so each variant is unambiguous.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _shared=OFF
else
    _shared=ON
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/Build" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${_shared}" \
    -DJPH_BUILD_SHARED_LIBS="${_shared}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_INSTALL_RPATH="\$ORIGIN;\$ORIGIN/../lib" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
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
    -DCROSS_PLATFORM_DETERMINISTIC=ON \
    -DPROFILER_IN_DEBUG_AND_RELEASE=OFF \
    -DJPH_USE_DX12=OFF \
    -DJPH_USE_VK=OFF \
    -DJPH_USE_MTL=OFF \
    -DUSE_AVX=OFF \
    -DUSE_AVX2=OFF \
    -DUSE_AVX512=OFF \
    -DUSE_LZCNT=OFF \
    -DUSE_TZCNT=OFF \
    -DUSE_F16C=OFF \
    -DUSE_FMADD=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Relocate absolute install-dir paths baked into JoltConfig.cmake so
# find_package(Jolt) works from any unpack prefix.
cvc_rewrite_install_paths
