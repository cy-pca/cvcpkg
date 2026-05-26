# recipes/boost/build-wasm.ps1 — cross-compile Boost to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DBOOST_ENABLE_CMAKE=ON',
    '-DBUILD_TESTING=OFF',
    '-DBOOST_INSTALL_LAYOUT=system',
    '-DCMAKE_CXX_FLAGS=-DBOOST_HAS_PTHREADS',
    '-DBOOST_EXCLUDE_LIBRARIES=context;coroutine;fiber;stacktrace;asio;cobalt'
)
