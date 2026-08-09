# recipes/brotli/build.ps1 — build Brotli from source with CMake.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    "-DBUILD_SHARED_LIBS=$buildSharedLibs",
    "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
    '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
    '-DBROTLI_DISABLE_TESTS=ON'
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }

Invoke-CvcRewriteInstallPaths
