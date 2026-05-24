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
    # gRPC's upb sub-libraries lack proper DLL symbol exports on
    # Windows (LNK2019 for upb_alloc_global etc.).  Build the entire
    # protobuf ecosystem as static libs -- the .lib files still use
    # /MD (dynamic CRT) so they link cleanly into shared consumers.
    '-DBUILD_SHARED_LIBS=OFF'
)
