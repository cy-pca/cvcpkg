# recipes/libxml2/build.ps1 — build libxml2 on Windows with CMake.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# Minimal feature set (see build.sh): zlib only; lzma/iconv/icu/python off;
# no CLI tools or tests shipped.
Invoke-CvcCMakeBuild @(
    '-DBUILD_SHARED_LIBS=ON',
    '-DLIBXML2_WITH_ZLIB=ON',
    '-DLIBXML2_WITH_LZMA=OFF',
    '-DLIBXML2_WITH_ICONV=OFF',
    '-DLIBXML2_WITH_ICU=OFF',
    '-DLIBXML2_WITH_PYTHON=OFF',
    '-DLIBXML2_WITH_PROGRAMS=OFF',
    '-DLIBXML2_WITH_TESTS=OFF'
)
