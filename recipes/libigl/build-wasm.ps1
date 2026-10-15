# recipes/libigl/build-wasm.ps1 — libigl for wasm from a Windows host.
# build.ps1 carries the whole procedure for both Windows-hosted targets and
# switches to env-wasm.ps1 / Invoke-CvcWasmCMakeBuild (SERIAL parallel_for,
# Emscripten smoke run under the emsdk's node) when CVC_PLATFORM is wasm.
$ErrorActionPreference = 'Stop'

if ($env:CVC_PLATFORM -ne 'wasm') { throw "build-wasm.ps1 builds wasm, not '$env:CVC_PLATFORM'" }
& (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'build.ps1')
