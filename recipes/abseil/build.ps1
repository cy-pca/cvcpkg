# recipes/abseil/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$extra = @(
    '-DABSL_BUILD_TESTING=OFF',
    '-DABSL_USE_GOOGLETEST_HEAD=OFF',
    '-DCMAKE_CXX_STANDARD=17',
    # Build abseil as static on Windows in all configurations.
    # gRPC's DLL builds are broken (upb export issues), so the
    # entire protobuf ecosystem ships as static .lib files.
    '-DBUILD_SHARED_LIBS=OFF'
)

# Abseil defaults ABSL_MSVC_STATIC_RUNTIME=OFF, which forces /MD even when
# CMAKE_MSVC_RUNTIME_LIBRARY requests /MT.  Align with protobuf (which
# defaults to static CRT for static libs).
if ($env:CVC_LINK -eq 'static') {
    $extra += '-DABSL_MSVC_STATIC_RUNTIME=ON'
}

Invoke-CvcCMakeBuild $extra
