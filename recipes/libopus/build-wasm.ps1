# recipes/libopus/build-wasm.ps1 — cross-compile libopus to wasm on a Windows host (see
# build-wasm.sh).  A Windows host has no autotools, so this builds with upstream's CMake, configured
# as build-wasm.sh configures autotools: static only, no programs/docs/tests, custom modes on, and
# no architecture-specific code (OPUS_DISABLE_INTRINSICS also drops runtime CPU detection; opus
# never uses its asm on wasm).  What is left is the reference C, as Emscripten's own port ships.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Invoke-CvcWasmCMakeBuild @(
    '-DOPUS_BUILD_SHARED_LIBRARY=OFF',
    '-DOPUS_BUILD_TESTING=OFF',
    '-DOPUS_BUILD_PROGRAMS=OFF',
    '-DOPUS_CUSTOM_MODES=ON',
    '-DOPUS_DISABLE_INTRINSICS=ON',
    '-DOPUS_INSTALL_PKG_CONFIG_MODULE=ON'
)
