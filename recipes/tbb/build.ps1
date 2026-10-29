# recipes/tbb/build.ps1 — build oneTBB, tbbmalloc and tbbmalloc_proxy on Windows via CMake + MSVC.
# Same switches as build.sh (TBB_STRICT off, no hwloc search).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DTBB_TEST=OFF',
    '-DTBB_EXAMPLES=OFF',
    '-DTBB_STRICT=OFF',
    '-DTBBMALLOC_BUILD=ON',
    '-DTBBMALLOC_PROXY_BUILD=ON',
    '-DTBB_DISABLE_HWLOC_AUTOMATIC_SEARCH=ON'
)
