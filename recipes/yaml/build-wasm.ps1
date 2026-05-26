# recipes/yaml/build-wasm.ps1 — cross-compile libyaml to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DYAML_BUILD_TESTING=OFF',
    '-DINSTALL_CMAKE_DIR=$env:CVC_INSTALL_DIR\lib\cmake\yaml'
)
