# recipes/protobuf/build-wasm.ps1 — cross-compile libprotobuf to wasm (see build-wasm.sh).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Invoke-CvcWasmCMakeBuild @(
    '-Dprotobuf_BUILD_TESTS=OFF',
    '-Dprotobuf_BUILD_EXAMPLES=OFF',
    '-Dprotobuf_BUILD_CONFORMANCE=OFF',
    '-Dprotobuf_BUILD_PROTOC_BINARIES=OFF',
    '-Dprotobuf_BUILD_LIBPROTOC=OFF',
    '-Dprotobuf_BUILD_LIBUPB=OFF',
    '-Dprotobuf_ABSL_PROVIDER=package',
    '-Dprotobuf_WITH_ZLIB=ON',
    '-DCMAKE_CXX_STANDARD=17'
)
