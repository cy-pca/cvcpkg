# recipes/openjph/build.ps1 — build the OpenJPH library on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DOJPH_BUILD_TESTS=OFF',
    '-DOJPH_BUILD_EXECUTABLES=OFF',
    '-DOJPH_ENABLE_TIFF_SUPPORT=OFF'
)
