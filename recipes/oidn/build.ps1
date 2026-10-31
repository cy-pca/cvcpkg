# recipes/oidn/build.ps1 — build Open Image Denoise (CPU device only) on Windows via CMake + MSVC.
# Same switches as build.sh (see the comment there).  ISPC is the build
# prefix's ispc.exe and Python its python.exe (python313), never a copy on PATH.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$roots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ } | Select-Object -Unique
function Find-CvcPrefixFile([string]$rel) {
    foreach ($root in $roots) {
        $cand = Join-Path $root $rel
        if (Test-Path -LiteralPath $cand) { return $cand }
    }
    throw "oidn: no $rel in CVC_BUILD_PREFIX ($env:CVC_BUILD_PREFIX) or CVC_DEPS_PREFIX ($env:CVC_DEPS_PREFIX)"
}
$ispc = (Find-CvcPrefixFile 'bin\ispc.exe') -replace '\\', '/'
$python = (Find-CvcPrefixFile 'python.exe') -replace '\\', '/'

$static = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }

Invoke-CvcCMakeBuild @(
    "-DISPC_EXECUTABLE=$ispc",
    "-DPython_EXECUTABLE=$python",
    '-DPython_FIND_REGISTRY=NEVER',
    "-DOIDN_STATIC_LIB=$static",
    '-DOIDN_DEVICE_CPU=ON',
    '-DOIDN_DEVICE_SYCL=OFF',
    '-DOIDN_DEVICE_CUDA=OFF',
    '-DOIDN_DEVICE_HIP=OFF',
    '-DOIDN_FILTER_RT=ON',
    '-DOIDN_FILTER_RTLIGHTMAP=ON',
    '-DOIDN_APPS=OFF',
    '-DOIDN_INSTALL_DEPENDENCIES=OFF',
    '-DOIDN_WARN_AS_ERRORS=OFF'
)
