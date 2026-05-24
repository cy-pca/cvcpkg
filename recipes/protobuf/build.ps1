# recipes/protobuf/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$extra = @(
    '-Dprotobuf_BUILD_TESTS=OFF',
    '-Dprotobuf_BUILD_EXAMPLES=OFF',
    '-Dprotobuf_BUILD_PROTOC_BINARIES=ON',
    '-Dprotobuf_BUILD_LIBPROTOC=ON',
    '-Dprotobuf_ABSL_PROVIDER=package',
    '-DCMAKE_CXX_STANDARD=17',
    # Static on Windows -- see grpc/build.ps1 comment.
    '-DBUILD_SHARED_LIBS=OFF'
)

# protobuf defaults protobuf_MSVC_STATIC_RUNTIME=ON, which forces /MT.
# When CVC_LINK=shared the rest of the stack uses /MD, so we must
# align protobuf's CRT choice to avoid LNK2019 for CRT imports
# (__imp_fgetpos etc.) from the /MD-compiled abseil static libs.
if ($env:CVC_LINK -ne 'static') {
    $extra += '-Dprotobuf_MSVC_STATIC_RUNTIME=OFF'
}

Invoke-CvcCMakeBuild $extra
