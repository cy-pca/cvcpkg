# recipes/zlib-ng/build.ps1 — build zlib-ng from source on Windows (MSVC).
# Native (non-compat) API, portable SIMD dispatch; see build.sh for the why.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DZLIB_COMPAT=OFF',
    '-DBUILD_TESTING=OFF',
    '-DWITH_GTEST=OFF',
    '-DWITH_NATIVE_INSTRUCTIONS=OFF',
    '-DWITH_RUNTIME_CPU_DETECTION=ON',
    '-DCMAKE_INSTALL_LIBDIR=lib'
)
