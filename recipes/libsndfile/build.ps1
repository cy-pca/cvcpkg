# recipes/libsndfile/build.ps1 — build libsndfile on Windows via CMake/MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DBUILD_PROGRAMS=OFF',
    '-DBUILD_EXAMPLES=OFF',
    '-DENABLE_EXTERNAL_LIBS=OFF',
    '-DENABLE_MPEG=OFF',
    '-DENABLE_CPACK=OFF',
    '-DINSTALL_MANPAGES=OFF'
)
