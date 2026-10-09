# recipes/eigen/build.ps1 — install Eigen (header-only) on Windows.
# Same switches as build.sh (see there for the package-registry and prerelease
# notes).  EIGEN_BUILD_PKGCONFIG is not even defined on a native Windows host, so
# no eigen3.pc is produced here (package.files scopes it).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"   # imports vcvars64 if cl.exe is not on PATH

Invoke-CvcCMakeBuild @(
    '-DCMAKE_EXPORT_PACKAGE_REGISTRY=OFF',
    '-DCMAKE_EXPORT_NO_PACKAGE_REGISTRY=ON',
    '-DEIGEN_PRERELEASE_VERSION=',
    '-DBUILD_TESTING=OFF',
    '-DEIGEN_BUILD_TESTING=OFF',
    '-DEIGEN_BUILD_BLAS=OFF',
    '-DEIGEN_BUILD_LAPACK=OFF',
    '-DEIGEN_BUILD_DOC=OFF',
    '-DEIGEN_BUILD_DEMOS=OFF',
    '-DEIGEN_BUILD_BTL=OFF',
    '-DEIGEN_BUILD_SPBENCH=OFF',
    '-DEIGEN_BUILD_CMAKE_PACKAGE=ON'
)

$version = Join-Path $env:CVC_INSTALL_DIR 'include\eigen3\Eigen\Version'
if (-not (Select-String -Quiet -SimpleMatch -LiteralPath $version -Pattern "EIGEN_VERSION_STRING `"$env:CVC_VERSION`"")) {
    throw "eigen: installed Eigen/Version is not $env:CVC_VERSION"
}

$licenses = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\eigen'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null
Copy-Item -Force -Path (Join-Path $env:CVC_SOURCE_DIR 'COPYING.*') -Destination $licenses
