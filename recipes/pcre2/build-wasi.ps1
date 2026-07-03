# recipes/pcre2/build-wasi.ps1 — cross-compile PCRE2 to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild @(
    '-DPCRE2_BUILD_PCRE2_8=ON',
    '-DPCRE2_BUILD_PCRE2_16=OFF',
    '-DPCRE2_BUILD_PCRE2_32=OFF',
    '-DPCRE2_SUPPORT_UNICODE=ON',
    '-DPCRE2_BUILD_PCRE2GREP=OFF',
    '-DPCRE2_BUILD_TESTS=OFF',
    '-DINSTALL_PKGCONFIG_DIR=$env:CVC_INSTALL_DIR\lib\pkgconfig'
)
