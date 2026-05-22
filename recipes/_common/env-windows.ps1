# recipes/_common/env-windows.ps1 — shared environment for Windows recipe builds.
#
# Dot-sourced by every build.ps1 on Windows.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_BUILD_TYPE)  { $env:CVC_BUILD_TYPE  = 'Release' }
if (-not $env:CVC_LINK)        { $env:CVC_LINK        = 'shared' }
if (-not $env:CVC_JOBS)        { $env:CVC_JOBS        = [Environment]::ProcessorCount }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_BUILD_DIR)   { throw 'CVC_BUILD_DIR must be set' }

$cmakeBuildType = switch ($env:CVC_BUILD_TYPE.ToLower()) {
    'release' { 'Release' }
    'debug'   { 'Debug' }
    default   { 'Release' }
}

$buildSharedLibs = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }

if ($env:CVC_DEPS_PREFIX) {
    $env:CMAKE_PREFIX_PATH = $env:CVC_DEPS_PREFIX
}

function Invoke-CvcCMakeBuild {
    param([string[]]$ExtraArgs = @())
    $allArgs = @(
        '-G', 'Ninja',
        '-S', $env:CVC_SOURCE_DIR,
        '-B', $env:CVC_BUILD_DIR,
        "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
        "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
        "-DBUILD_SHARED_LIBS=$buildSharedLibs"
    ) + $ExtraArgs

    & cmake @allArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    & cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

    & cmake --install $env:CVC_BUILD_DIR
    if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
}

Write-Host "-- env-windows.ps1 loaded --"
Write-Host "  BUILD_TYPE=$cmakeBuildType  LINK=$env:CVC_LINK  JOBS=$env:CVC_JOBS"
