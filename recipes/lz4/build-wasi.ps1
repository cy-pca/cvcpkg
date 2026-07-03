# recipes/lz4/build-wasi.ps1 — cross-compile lz4 to wasm32-wasi via wasi-sdk.
# lz4's cmake project lives under build\cmake\.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild -SourceDir (Join-Path $env:CVC_SOURCE_DIR 'build\cmake') -ExtraArgs @(
    '-DLZ4_BUILD_CLI=OFF',
    '-DLZ4_BUILD_LEGACY_LZ4C=OFF'
)
