# recipes/mpfr/build-wasi.ps1 — cross-compile MPFR to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

$env:CC_FOR_BUILD = 'gcc'

$msysDeps = ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX
Invoke-CvcWasiAutotoolsBuild -ConfigureArgs @("--with-gmp='$msysDeps'")
