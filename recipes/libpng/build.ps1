# recipes/libpng/build.ps1 — build libpng from source on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DPNG_TESTS=OFF',
    '-DPNG_TOOLS=OFF',
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX"
)
