# recipes/yaml/build-wasi.ps1 — cross-compile libyaml to wasm32-wasi.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild @(
    '-DYAML_BUILD_TESTING=OFF',
    "-DINSTALL_CMAKE_DIR=$env:CVC_INSTALL_DIR\lib\cmake\yaml"
)
