# recipes/libdeflate/build.ps1 — build libdeflate (shared) on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DLIBDEFLATE_BUILD_SHARED_LIB=ON',
    '-DLIBDEFLATE_BUILD_STATIC_LIB=OFF',
    '-DLIBDEFLATE_BUILD_GZIP=OFF',
    '-DLIBDEFLATE_BUILD_TESTS=OFF'
)
