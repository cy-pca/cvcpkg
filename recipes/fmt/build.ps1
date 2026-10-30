# recipes/fmt/build.ps1 — build {fmt} on Windows via CMake + MSVC.
# BUILD_SHARED_LIBS (from CVC_LINK) makes fmt.dll; fmt's CMake export carries
# FMT_SHARED to consumers, so they import the symbols.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DFMT_TEST=OFF',
    '-DFMT_DOC=OFF',
    '-DFMT_INSTALL=ON'
)
