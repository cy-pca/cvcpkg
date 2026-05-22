# recipes/protobuf/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-Dprotobuf_BUILD_TESTS=OFF',
    '-Dprotobuf_BUILD_EXAMPLES=OFF',
    '-Dprotobuf_BUILD_PROTOC_BINARIES=ON',
    '-Dprotobuf_BUILD_LIBPROTOC=ON',
    '-Dprotobuf_ABSL_PROVIDER=package',
    '-DCMAKE_CXX_STANDARD=17'
)
