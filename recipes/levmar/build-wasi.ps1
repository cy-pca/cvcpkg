# recipes/levmar/build-wasi.ps1 — cross-compile levmar to wasm32-wasi via wasi-sdk on Windows.
# Uses CLAPACK (wasi build) for BLAS/LAPACK.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

$deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }

Invoke-CvcWasiCMakeBuild -ExtraArgs @(
    "-DCMAKE_PREFIX_PATH=$deps",
    '-DUSE_BLAS=ON'
)
