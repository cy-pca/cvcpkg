# recipes/tiff/build-wasm.ps1 — cross-compile libtiff to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-Dtiff-tests=OFF',
    '-Dtiff-tools=OFF',
    '-Dtiff-contrib=OFF',
    '-Dtiff-docs=OFF',
    '-Djbig=OFF',
    '-Dlibdeflate=OFF'
)
