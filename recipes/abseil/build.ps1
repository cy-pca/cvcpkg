# recipes/abseil/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$extra = @(
    '-DABSL_BUILD_TESTING=OFF',
    '-DABSL_USE_GOOGLETEST_HEAD=OFF',
    '-DCMAKE_CXX_STANDARD=17'
)

# Abseil defaults ABSL_MSVC_STATIC_RUNTIME=OFF, which forces /MD even when
# CMAKE_MSVC_RUNTIME_LIBRARY requests /MT.  Align with protobuf (which
# defaults to static CRT for static libs).
if ($env:CVC_LINK -eq 'static') {
    $extra += '-DABSL_MSVC_STATIC_RUNTIME=ON'
}

Invoke-CvcCMakeBuild $extra
