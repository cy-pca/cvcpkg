# recipes/levmar/build-wasm.ps1 — cross-compile levmar to wasm.
# Uses CLAPACK (f2c'd C LAPACK) for BLAS/LAPACK on wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$levmarSrc = Join-Path $scriptDir '..\..\third-party\levmar'

Invoke-CvcWasmCMakeBuild -SourceDir $levmarSrc -ExtraArgs @(
    '-DUSE_BLAS=ON',
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX"
)
