# recipes/c-ares/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DCARES_BUILD_TESTS=OFF',
    '-DCARES_BUILD_TOOLS=OFF',
    # Force c-ares to typedef ares_ssize_t as __int64 on Windows.
    # Recent MSVC (VS 17.6+) exposes ssize_t via corecrt.h/POSIX
    # compat, so CARES_TYPE_EXISTS(ssize_t) succeeds and c-ares
    # emits 'typedef ssize_t ares_ssize_t;' in the public header.
    # ssize_t then is not defined when downstream consumers (grpc)
    # include ares.h, giving MSVC C2059 / C2091 / C2116.
    # Pre-seed the cache with FALSE so c-ares picks the __int64
    # fallback (line 603 of upstream CMakeLists.txt).
    '-DHAVE_SSIZE_T:INTERNAL=FALSE'
)
