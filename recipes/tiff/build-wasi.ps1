# recipes/tiff/build-wasi.ps1 — cross-compile libtiff to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild @(
    '-Dtiff-tests=OFF',
    '-Dtiff-tools=OFF',
    '-Dtiff-contrib=OFF',
    '-Dtiff-docs=OFF',
    '-Djbig=OFF',
    '-Dlibdeflate=OFF'
)
