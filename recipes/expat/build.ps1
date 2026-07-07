# recipes/expat/build.ps1 — build Expat from source on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DEXPAT_BUILD_TESTS=OFF',
    '-DEXPAT_BUILD_EXAMPLES=OFF',
    '-DEXPAT_BUILD_TOOLS=ON',
    '-DEXPAT_BUILD_DOCS=OFF'
)
