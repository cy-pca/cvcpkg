# recipes/libuv/build.ps1 — build libuv from source on Windows (MSVC).
# Installs uv.dll (bin/) + uv.lib (import) and the static libuv.lib.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$shared = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }

Invoke-CvcCMakeBuild @(
    "-DLIBUV_BUILD_SHARED=$shared",
    '-DLIBUV_BUILD_TESTS=OFF',
    '-DLIBUV_BUILD_BENCH=OFF',
    '-DENABLE_CLANG_TIDY=OFF',
    '-DCMAKE_INSTALL_LIBDIR=lib'
)
