# recipes/x265/build.ps1 — build x265 H.265/HEVC encoder on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Import-CvcMsvcEnv

$shared = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }
$static = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }

# Put nasm (from deps) on PATH for assembly optimisations.
if ($env:CVC_DEPS_PREFIX) {
    $env:PATH = "$env:CVC_DEPS_PREFIX\bin;$env:PATH"
}

& cmake -G Ninja `
    -S "$env:CVC_SOURCE_DIR\source" `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON `
    "-DBUILD_SHARED_LIBS=$shared" `
    "-DENABLE_SHARED=$shared" `
    "-DENABLE_STATIC=$static" `
    -DENABLE_CLI=OFF `
    -DENABLE_TESTS=OFF `
    -DLIB_INSTALL_DIR=lib
if ($LASTEXITCODE -ne 0) { throw 'x265 cmake configure failed' }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'x265 cmake build failed' }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw 'x265 cmake install failed' }

Invoke-CvcRewriteInstallPaths
