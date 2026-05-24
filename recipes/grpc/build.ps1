# recipes/grpc/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DgRPC_BUILD_TESTS=OFF',
    '-DgRPC_BUILD_CSHARP_EXT=OFF',
    '-DgRPC_BUILD_GRPC_CPP_PLUGIN=ON',
    '-DgRPC_BUILD_GRPC_CSHARP_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_NODE_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_OBJECTIVE_C_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_PHP_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_PYTHON_PLUGIN=ON',
    '-DgRPC_BUILD_GRPC_RUBY_PLUGIN=OFF',
    '-DgRPC_ABSL_PROVIDER=package',
    '-DgRPC_CARES_PROVIDER=package',
    '-DgRPC_PROTOBUF_PROVIDER=package',
    '-DgRPC_RE2_PROVIDER=package',
    '-DgRPC_SSL_PROVIDER=package',
    '-DgRPC_ZLIB_PROVIDER=package',
    '-DCMAKE_CXX_STANDARD=17',
    # gRPC's upb sub-libraries lack proper __declspec(dllexport)
    # annotations, so CMake-generated .def files miss data symbols
    # like upb_alloc_global.  Let CMake auto-export everything.
    '-DCMAKE_WINDOWS_EXPORT_ALL_SYMBOLS=ON'
)
