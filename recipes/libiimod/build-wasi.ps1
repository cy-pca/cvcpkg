# recipes/libiimod/build-wasi.ps1 — cross-compile libiimod to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

$libiimodSrc = Join-Path $scriptDir '..\..\third-party\libiimod'

Invoke-CvcWasiCMakeBuild -SourceDir $libiimodSrc
