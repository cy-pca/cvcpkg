# recipes/boost/build-wasi.ps1 — cross-compile Boost to wasm32-wasi.
# Windows-host twin of build-wasi.sh; see that script for why each library
# is excluded on wasm32-wasip1.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild -ExtraArgs @(
    '-DBOOST_ENABLE_CMAKE=ON',
    '-DBUILD_TESTING=OFF',
    '-DBOOST_INSTALL_LAYOUT=system',
    '-DBOOST_EXCLUDE_LIBRARIES=context;coroutine;fiber;stacktrace;asio;cobalt;log;process;beast;thread;contract;test;locale;wave;filesystem'
)
