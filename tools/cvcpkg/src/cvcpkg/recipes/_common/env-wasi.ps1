# recipes/_common/env-wasi.ps1 — shared environment for wasi cross-compilation on Windows.
#
# Dot-sourced by build-wasi.ps1 scripts.  Loads env-windows.ps1 first,
# then configures the wasi-sdk toolchain and provides Invoke-CvcWasiCMakeBuild.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\env-windows.ps1"

if (-not $env:CVC_WASI_SDK_DIR) { throw 'CVC_WASI_SDK_DIR must point to the installed wasi-sdk' }

# WASI builds are always static.
$env:CVC_LINK = 'static'
$buildSharedLibs = 'OFF'

# Set compiler environment variables.
$env:CC = Join-Path $env:CVC_WASI_SDK_DIR 'bin\clang.exe'
$env:CXX = Join-Path $env:CVC_WASI_SDK_DIR 'bin\clang++.exe'
$env:AR = Join-Path $env:CVC_WASI_SDK_DIR 'bin\llvm-ar.exe'
$env:RANLIB = Join-Path $env:CVC_WASI_SDK_DIR 'bin\llvm-ranlib.exe'

# Locate the wasi-sdk CMake toolchain file.
$wasiToolchain = Join-Path $env:CVC_WASI_SDK_DIR 'share\cmake\wasi-sdk.cmake'
$hasToolchainFile = Test-Path $wasiToolchain
$wasiSysroot = Join-Path $env:CVC_WASI_SDK_DIR 'share\wasi-sysroot'

function Invoke-CvcWasiCMakeBuild {
    param(
        [string[]]$ExtraArgs = @(),
        [string]$SourceDir = $env:CVC_SOURCE_DIR
    )
    $findRootPathArgs = @()
    if ($env:CVC_DEPS_PREFIX) {
        $findRootPathArgs += "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX"
    }

    $toolchainArgs = @()
    if ($hasToolchainFile) {
        $toolchainArgs += "-DCMAKE_TOOLCHAIN_FILE=$wasiToolchain"
    } else {
        $toolchainArgs += @(
            '-DCMAKE_SYSTEM_NAME=WASI',
            '-DCMAKE_SYSTEM_PROCESSOR=wasm32',
            "-DCMAKE_C_COMPILER=$env:CC",
            "-DCMAKE_CXX_COMPILER=$env:CXX",
            "-DCMAKE_AR=$env:AR",
            "-DCMAKE_RANLIB=$env:RANLIB",
            "-DCMAKE_SYSROOT=$wasiSysroot",
            '-DCMAKE_C_COMPILER_TARGET=wasm32-wasip1',
            '-DCMAKE_CXX_COMPILER_TARGET=wasm32-wasip1'
        )
    }

    $allArgs = @(
        '-G', 'Ninja',
        '-S', $SourceDir,
        '-B', $env:CVC_BUILD_DIR,
        "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
        "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
        '-DBUILD_SHARED_LIBS=OFF',
        '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
        '-DCMAKE_CXX_STANDARD=17',
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'
    ) + $toolchainArgs + $findRootPathArgs + $ExtraArgs

    & cmake @allArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    & cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

    & cmake --install $env:CVC_BUILD_DIR
    if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
}

Write-Host "-- env-wasi.ps1 loaded --"
Write-Host "  WASI_SDK=$env:CVC_WASI_SDK_DIR  BUILD_TYPE=$cmakeBuildType  LINK=static  JOBS=$env:CVC_JOBS"
