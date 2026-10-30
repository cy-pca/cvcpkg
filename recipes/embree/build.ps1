# recipes/embree/build.ps1 — build the Embree 4 ray-tracing library on Windows via CMake + MSVC.
# Same switches as build.sh (see the comment there).
#
# Embree's package.cmake installs the MSVC runtime (msvcp140*.dll,
# vcruntime140*.dll) next to embree4.dll on every Windows install.  The bundle
# must not carry private copies of Microsoft's runtime -- consumers use the
# system's VC++ redistributable, like every other cvcpkg DLL -- so they are
# deleted from the install tree.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$static = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }

Invoke-CvcCMakeBuild @(
    "-DEMBREE_STATIC_LIB=$static",
    '-DEMBREE_TUTORIALS=OFF',
    '-DEMBREE_ISPC_SUPPORT=OFF',
    '-DEMBREE_SYCL_SUPPORT=OFF',
    '-DEMBREE_TASKING_SYSTEM=TBB',
    '-DEMBREE_RAY_MASK=ON',
    '-DEMBREE_FILTER_FUNCTION=ON',
    '-DEMBREE_BACKFACE_CULLING=OFF',
    '-DEMBREE_BACKFACE_CULLING_CURVES=ON',
    '-DEMBREE_BACKFACE_CULLING_SPHERES=ON',
    '-DEMBREE_ZIP_MODE=OFF',
    '-DEMBREE_INSTALL_DEPENDENCIES=OFF',
    '-DBUILD_TESTING=OFF',
    '-DEMBREE_MAX_ISA=AVX2'
)

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
foreach ($pattern in @('msvcp140*.dll', 'vcruntime140*.dll', 'concrt140*.dll')) {
    Get-ChildItem -LiteralPath $bin -Filter $pattern -File -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "embree: removing bundled MSVC runtime $($_.Name)"
        Remove-Item -LiteralPath $_.FullName -Force
    }
}
