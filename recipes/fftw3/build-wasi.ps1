# recipes/fftw3/build-wasi.ps1 — cross-compile FFTW3 to wasm32-wasi via wasi-sdk.
# Two cmake passes: double precision, then single precision (float).
# Threading disabled for wasi.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

# Build toolchain args once, matching env-wasi.ps1's Invoke-CvcWasiCMakeBuild.
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

$commonArgs = @(
    '-DBUILD_TESTS=OFF',
    '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
    '-DENABLE_THREADS=OFF',
    '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'
) + $toolchainArgs

# Pass 1: double precision
$doubleDir = "$env:CVC_BUILD_DIR\double"
$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $doubleDir,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DBUILD_SHARED_LIBS=OFF'
) + $commonArgs

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure (double) failed" }
& cmake --build $doubleDir -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build (double) failed" }
& cmake --install $doubleDir
if ($LASTEXITCODE -ne 0) { throw "cmake install (double) failed" }

# Pass 2: single precision (float)
$floatDir = "$env:CVC_BUILD_DIR\float"
$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $floatDir,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DBUILD_SHARED_LIBS=OFF',
    '-DENABLE_FLOAT=ON'
) + $commonArgs

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure (float) failed" }
& cmake --build $floatDir -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build (float) failed" }
& cmake --install $floatDir
if ($LASTEXITCODE -ne 0) { throw "cmake install (float) failed" }

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
