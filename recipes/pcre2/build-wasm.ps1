# recipes/pcre2/build-wasm.ps1 — cross-compile PCRE2 to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DPCRE2_BUILD_PCRE2_8=ON',
    '-DPCRE2_BUILD_PCRE2_16=ON',
    '-DPCRE2_BUILD_PCRE2_32=OFF',
    '-DPCRE2_SUPPORT_UNICODE=ON',
    '-DPCRE2_BUILD_PCRE2GREP=OFF',
    '-DPCRE2_BUILD_TESTS=OFF',
    '-DINSTALL_PKGCONFIG_DIR=$env:CVC_INSTALL_DIR\lib\pkgconfig'
)
