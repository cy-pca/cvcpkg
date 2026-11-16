# recipes/websocketpp/build.ps1 -- install WebSocket++ (header-only) on Windows (see build.sh).
# Upstream's Windows default for the CMake config is cmake/; INSTALL_CMAKE_DIR moves it to where it
# is on unix. It must be absolute: a relative PATH cache entry is resolved against the build dir.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_EXAMPLES=OFF',
    '-DBUILD_TESTS=OFF',
    "-DINSTALL_CMAKE_DIR=$($env:CVC_INSTALL_DIR -replace '\\', '/')/lib/cmake/websocketpp"
)
