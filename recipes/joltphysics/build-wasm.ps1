# recipes/joltphysics/build-wasm.ps1 — cross-compile Jolt Physics to wasm via
# Emscripten from a Windows host.  Mirrors build-wasm.sh (linux host) so the
# Windows builder fleet (and a dev box holding the emsdk bundle) can produce
# the wasm variant; lerc/zstd carry the same host_platform: windows entry.
#
# Jolt's CMake project root is Build/, not the tarball root.  Every TARGET_*
# app is off; only the core library is built (static — wasm is always static).
#
# Under Emscripten Jolt's own Jolt.cmake takes its `elseif (EMSCRIPTEN)` branch
# BEFORE the x86 branch, so USE_SSE4_x/USE_AVX*/USE_FMADD never reach the
# compiler here; they are pinned OFF anyway so the recipe reads identically on
# every cross script.  USE_WASM_SIMD is left at its default (OFF): the scalar
# Vec4/Mat44 path is the configuration upstream verifies for cross-platform
# determinism ("WASM32 emscripten running in nodejs" in Docs/Architecture.md),
# and it keeps consumers free of a -msimd128 requirement.
#
# CROSS_PLATFORM_DETERMINISTIC=ON: the digital-twin use is "authoritative
# native solver, browser replay of the same inputs"; upstream guarantees
# identical results across compilers/OS/arch (incl. emscripten) only with this
# on, at ~8% cost.  It is a JPH_VERSION_ID feature bit, so it MUST match the
# native builds (build.sh / build.ps1 / build-cosmo.sh set it too, cvc.4).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$joltSrc = Join-Path $env:CVC_SOURCE_DIR 'Build'

Invoke-CvcWasmCMakeBuild -SourceDir $joltSrc -ExtraArgs @(
    '-DCMAKE_INSTALL_LIBDIR=lib',
    '-DJPH_BUILD_SHARED_LIBS=OFF',
    '-DENABLE_INSTALL=ON',
    '-DTARGET_UNIT_TESTS=OFF',
    '-DTARGET_HELLO_WORLD=OFF',
    '-DTARGET_PERFORMANCE_TEST=OFF',
    '-DTARGET_SAMPLES=OFF',
    '-DTARGET_VIEWER=OFF',
    '-DOVERRIDE_CXX_FLAGS=OFF',
    '-DGENERATE_DEBUG_SYMBOLS=OFF',
    '-DFLOATING_POINT_EXCEPTIONS_ENABLED=OFF',
    '-DINTERPROCEDURAL_OPTIMIZATION=OFF',
    '-DENABLE_ALL_WARNINGS=OFF',
    '-DCROSS_PLATFORM_DETERMINISTIC=ON',
    '-DPROFILER_IN_DEBUG_AND_RELEASE=OFF',
    '-DJPH_USE_DX12=OFF',
    '-DJPH_USE_VK=OFF',
    '-DJPH_USE_MTL=OFF',
    '-DUSE_SSE4_1=OFF',
    '-DUSE_SSE4_2=OFF',
    '-DUSE_AVX=OFF',
    '-DUSE_AVX2=OFF',
    '-DUSE_AVX512=OFF',
    '-DUSE_LZCNT=OFF',
    '-DUSE_TZCNT=OFF',
    '-DUSE_F16C=OFF',
    '-DUSE_FMADD=OFF'
)
