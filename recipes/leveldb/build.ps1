# recipes/leveldb/build.ps1 -- CMake build of LevelDB on Windows (see build.sh).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DLEVELDB_BUILD_TESTS=OFF',
    '-DLEVELDB_BUILD_BENCHMARKS=OFF',
    '-DLEVELDB_INSTALL=ON',
    '-DHAVE_CRC32C=OFF',
    '-DHAVE_TCMALLOC=OFF'
)
