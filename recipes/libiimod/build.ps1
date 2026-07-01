# recipes/libiimod/build.ps1 — build libiimod from vendored sources on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# CVC_SOURCE_DIR is set by the builder to the vendored source tree
# (bundled under _vendored/third-party/libiimod/ when running on a
# remote builder, or resolved from the repo root when building locally).
$libiimodSrc = $env:CVC_SOURCE_DIR

& cmake -G Ninja `
    -S $libiimodSrc `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType"
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
