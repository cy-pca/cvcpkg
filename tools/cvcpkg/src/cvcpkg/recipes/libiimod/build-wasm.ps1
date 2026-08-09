# recipes/libiimod/build-wasm.ps1 — cross-compile libiimod to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$libiimodSrc = Join-Path $scriptDir '..\..\third-party\libiimod'

Invoke-CvcWasmCMakeBuild -SourceDir $libiimodSrc
