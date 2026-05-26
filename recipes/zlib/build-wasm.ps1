# recipes/zlib/build-wasm.ps1 — cross-compile zlib to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DZLIB_BUILD_EXAMPLES=OFF',
    '-DINSTALL_PKGCONFIG_DIR=$env:CVC_INSTALL_DIR\lib\pkgconfig'
)
