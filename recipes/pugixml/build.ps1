# recipes/pugixml/build.ps1 — build pugixml on Windows via CMake + MSVC.
# A shared build exports its API with __declspec through PUGIXML_API, which
# pugixml's CMake sets for the library and its consumers.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DPUGIXML_BUILD_TESTS=OFF',
    '-DPUGIXML_BUILD_SHARED_AND_STATIC_LIBS=OFF',
    '-DPUGIXML_USE_VERSIONED_LIBDIR=OFF',
    '-DPUGIXML_USE_POSTFIX=OFF'
)
