# recipes/openexr/build.ps1 — build the OpenEXR libraries on Windows via CMake + MSVC.
# Same switches as build.sh: the cvcpkg imath, libdeflate and openjph come from
# CMAKE_PREFIX_PATH (set by env-windows.ps1), never OpenEXR's vendored copies.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DBUILD_WEBSITE=OFF',
    '-DOPENEXR_BUILD_TOOLS=OFF',
    '-DOPENEXR_INSTALL_TOOLS=OFF',
    '-DOPENEXR_BUILD_EXAMPLES=OFF',
    '-DOPENEXR_BUILD_PYTHON=OFF',
    '-DOPENEXR_BUILD_BOTH_STATIC_SHARED=OFF',
    '-DOPENEXR_FORCE_INTERNAL_DEFLATE=OFF',
    '-DOPENEXR_FORCE_INTERNAL_OPENJPH=OFF',
    '-DOPENEXR_FORCE_INTERNAL_IMATH=OFF'
)
