# recipes/joltphysics/build.ps1 — build Jolt Physics on Windows via CMake + MSVC.
#
# Jolt's CMake project root is the Build/ subdirectory; every TARGET_* app is
# turned off so only the core library is built.  GPU debug-renderer backends
# (DX12/Vulkan/Metal) are disabled to keep the physics library dependency-free,
# and USE_STATIC_MSVC_RUNTIME_LIBRARY is off so the harness's chosen runtime
# (CMAKE_MSVC_RUNTIME_LIBRARY) is honoured instead of Jolt forcing the static CRT.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$env:CVC_SOURCE_DIR = Join-Path $env:CVC_SOURCE_DIR 'Build'

Invoke-CvcCMakeBuild @(
    '-DCMAKE_CXX_STANDARD=17',
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
    # -Werror / MSVC /WX off: don't gate our build on upstream warning cleanliness
    # across the whole builder fleet (a stricter compiler otherwise fails it).
    '-DENABLE_ALL_WARNINGS=OFF',
    '-DCROSS_PLATFORM_DETERMINISTIC=ON',
    '-DPROFILER_IN_DEBUG_AND_RELEASE=OFF',
    '-DUSE_STATIC_MSVC_RUNTIME_LIBRARY=OFF',
    '-DJPH_USE_DX12=OFF',
    '-DJPH_USE_VK=OFF',
    '-DJPH_USE_MTL=OFF',
    '-DUSE_AVX=OFF',
    '-DUSE_AVX2=OFF',
    '-DUSE_AVX512=OFF',
    '-DUSE_LZCNT=OFF',
    '-DUSE_TZCNT=OFF',
    '-DUSE_F16C=OFF',
    '-DUSE_FMADD=OFF'
)
