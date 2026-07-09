# recipes/libgeos/build.ps1 — GEOS (libgeos + libgeos_c) via CMake (root CMakeLists).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DBUILD_BENCHMARKS=OFF'
)
