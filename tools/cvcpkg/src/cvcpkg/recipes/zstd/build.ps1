# recipes/zstd/build.ps1 — zstd's cmake project lives under build/cmake/.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$buildStatic = if ($buildSharedLibs -eq 'ON') { 'OFF' } else { 'ON' }

$allArgs = @(
    '-G', 'Ninja',
    '-S', "$env:CVC_SOURCE_DIR/build/cmake",
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    "-DBUILD_SHARED_LIBS=$buildSharedLibs",
    "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    '-DZSTD_BUILD_PROGRAMS=OFF',
    '-DZSTD_BUILD_CONTRIB=OFF',
    '-DZSTD_BUILD_TESTS=OFF',
    "-DZSTD_BUILD_STATIC=$buildStatic",
    "-DZSTD_BUILD_SHARED=$buildSharedLibs"
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
