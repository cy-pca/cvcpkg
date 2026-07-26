# recipes/assimp/build.ps1 — build Open Asset Import Library (assimp) on Windows via CMake + MSVC.
#
# assimp honors BUILD_SHARED_LIBS (set from CVC_LINK by Invoke-CvcCMakeBuild).
# On MSVC the library is emitted with a toolset suffix (e.g. assimp-vc143-mt),
# which package.files captures via lib/assimp* + bin/assimp*.  We use the
# cvcpkg zlib (-DASSIMP_BUILD_ZLIB=OFF => find_package(ZLIB) on CMAKE_PREFIX_PATH);
# other contrib deps stay vendored.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX",
    '-DASSIMP_BUILD_TESTS=OFF',
    '-DASSIMP_BUILD_ASSIMP_TOOLS=OFF',
    '-DASSIMP_INSTALL_PDB=OFF',
    '-DASSIMP_WARNINGS_AS_ERRORS=OFF',
    '-DASSIMP_BUILD_ZLIB=OFF'
)
