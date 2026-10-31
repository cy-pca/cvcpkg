# recipes/openpgl/build.ps1 — build the Open Path Guiding Library on Windows via CMake + MSVC.
# Same switches as build.sh (see the comment there).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$static = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }

Invoke-CvcCMakeBuild @(
    "-DOPENPGL_BUILD_STATIC=$static",
    '-DOPENPGL_BUILD_TOOLS=OFF',
    '-DOPENPGL_BUILD_CHECK_TOOL=OFF',
    '-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON'
)
