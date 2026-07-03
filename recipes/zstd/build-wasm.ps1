# recipes/zstd/build-wasm.ps1 — cross-compile zstd to wasm. CMake source is in build/cmake/.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Invoke-CvcWasmCMakeBuild -SourceDir "$env:CVC_SOURCE_DIR\build\cmake" -ExtraArgs @(
    '-DZSTD_BUILD_PROGRAMS=OFF',
    '-DZSTD_BUILD_CONTRIB=OFF',
    '-DZSTD_BUILD_TESTS=OFF',
    '-DZSTD_BUILD_STATIC=ON',
    '-DZSTD_BUILD_SHARED=OFF'
)

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
