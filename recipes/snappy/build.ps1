# recipes/snappy/build.ps1 -- CMake build of Snappy on Windows (see build.sh).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DSNAPPY_BUILD_TESTS=OFF',
    '-DSNAPPY_BUILD_BENCHMARKS=OFF',
    '-DSNAPPY_FUZZING_BUILD=OFF',
    '-DSNAPPY_INSTALL=ON'
)
