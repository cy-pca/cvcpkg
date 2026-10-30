# recipes/pystring/build.ps1 — build pystring on Windows with the recipe's own CMakeLists.txt.
# The library is static whatever CVC_LINK says; it links with the same MSVC
# runtime as everything else (CMAKE_MSVC_RUNTIME_LIBRARY from env-windows.ps1).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Copy-Item -Force (Join-Path $scriptDir 'CMakeLists.txt') (Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt')

Invoke-CvcCMakeBuild @()
