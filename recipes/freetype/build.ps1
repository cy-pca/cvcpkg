# recipes/freetype/build.ps1 — build FreeType from source on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX",
    '-DFT_REQUIRE_ZLIB=ON',
    '-DFT_REQUIRE_PNG=ON',
    '-DFT_REQUIRE_BZIP2=ON',
    '-DFT_DISABLE_HARFBUZZ=ON',
    '-DFT_DISABLE_BROTLI=ON'
)
