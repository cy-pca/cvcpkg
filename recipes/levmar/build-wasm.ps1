# recipes/levmar/build-wasm.ps1 — cross-compile levmar to wasm.
# Uses CLAPACK (f2c'd C LAPACK) for BLAS/LAPACK on wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

# The vendored source is staged at CVC_SOURCE_DIR (as build-wasm.sh uses it);
# third-party\levmar exists only in a checkout, not in a builder's recipe bundle.
$levmarSrc = $env:CVC_SOURCE_DIR

Invoke-CvcWasmCMakeBuild -SourceDir $levmarSrc -ExtraArgs @(
    '-DUSE_BLAS=ON',
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX",
    "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX"
)
