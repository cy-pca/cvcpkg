# recipes/wasmedge/build.ps1 — build WasmEdge from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DWASMEDGE_USE_LLVM=OFF',
    '-DWASMEDGE_BUILD_TESTS=OFF',
    '-DWASMEDGE_BUILD_TOOLS=ON',
    '-DWASMEDGE_BUILD_PLUGINS=OFF',
    '-DWASMEDGE_BUILD_SHARED_LIB=ON',
    '-DWASMEDGE_BUILD_STATIC_LIB=ON'
)
