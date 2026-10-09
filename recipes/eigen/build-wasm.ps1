# recipes/eigen/build-wasm.ps1 — install Eigen (header-only) for wasm from a
# Windows host.  Mirrors build.sh, which the linux-hosted wasm entry runs through
# env-wasm.sh: the Emscripten toolchain only has to pass Eigen's compiler checks.
# Under that toolchain WIN32 is unset, so EIGEN_BUILD_PKGCONFIG exists and
# eigen3.pc is produced exactly as on the linux host.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Invoke-CvcWasmCMakeBuild @(
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
    '-DEIGEN_BUILD_PKGCONFIG=ON',
    '-DEIGEN_BUILD_CMAKE_PACKAGE=ON'
)

$version = Join-Path $env:CVC_INSTALL_DIR 'include\eigen3\Eigen\Version'
if (-not (Select-String -Quiet -SimpleMatch -LiteralPath $version -Pattern "EIGEN_VERSION_STRING `"$env:CVC_VERSION`"")) {
    throw "eigen: installed Eigen/Version is not $env:CVC_VERSION"
}

$licenses = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\eigen'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null
Copy-Item -Force -Path (Join-Path $env:CVC_SOURCE_DIR 'COPYING.*') -Destination $licenses
