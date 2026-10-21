# recipes/levmar/build.ps1 — build levmar from vendored sources on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# The vendored source (recipe.yaml: source.type vendored) is staged at
# CVC_SOURCE_DIR, as build.sh uses it.  The repo-relative third-party\levmar
# exists only in a checkout, not in the recipe bundle a builder downloads
# ("source directory ... third-party/levmar does not exist", populate 2026-10-19).
$levmarSrc = $env:CVC_SOURCE_DIR

& cmake -G Ninja `
    -S $levmarSrc `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType"
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
