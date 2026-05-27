# recipes/_common/env-wasm.ps1 — shared environment for wasm cross-compilation on Windows.
#
# Dot-sourced by build-wasm.ps1 scripts.  Loads env-windows.ps1 first,
# then activates Emscripten and provides Invoke-CvcWasmCMakeBuild.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\env-windows.ps1"

if (-not $env:CVC_EMSDK_DIR) { throw 'CVC_EMSDK_DIR must point to the activated emsdk bundle' }

# Remove MSYS2/Git Bash env vars that confuse emsdk.py into emitting
# UNIX-format paths (`:` separator, `/c/` prefixes) inside PowerShell.
# This happens when the cvcpkg workflow step uses `shell: bash` on Windows.
Remove-Item Env:\MSYSTEM -ErrorAction SilentlyContinue
Remove-Item Env:\MSYSTEM_PREFIX -ErrorAction SilentlyContinue
Remove-Item Env:\MSYSTEM_CHOST -ErrorAction SilentlyContinue

# Activate Emscripten.
$emsdkEnv = Join-Path $env:CVC_EMSDK_DIR 'emsdk_env.ps1'
if (-not (Test-Path $emsdkEnv)) {
    # Fallback: emsdk_env.bat via cmd interop
    $emsdkBat = Join-Path $env:CVC_EMSDK_DIR 'emsdk_env.bat'
    if (-not (Test-Path $emsdkBat)) { throw "Cannot find emsdk_env.ps1 or emsdk_env.bat in $env:CVC_EMSDK_DIR" }
    & cmd /c "`"$emsdkBat`" && set" | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
} else {
    . $emsdkEnv
}

# Wasm builds are always static.
$env:CVC_LINK = 'static'
$buildSharedLibs = 'OFF'

# Locate the Emscripten toolchain file.
$emscriptenToolchain = Join-Path $env:CVC_EMSDK_DIR 'upstream\emscripten\cmake\Modules\Platform\Emscripten.cmake'
if (-not (Test-Path $emscriptenToolchain)) { throw "Emscripten toolchain file not found: $emscriptenToolchain" }

function Invoke-CvcWasmCMakeBuild {
    param(
        [string[]]$ExtraArgs = @(),
        [string]$SourceDir = $env:CVC_SOURCE_DIR
    )
    $findRootPathArgs = @()
    if ($env:CVC_DEPS_PREFIX) {
        $findRootPathArgs += "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX"
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
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
        "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain"
    ) + $findRootPathArgs + $ExtraArgs

    & cmake @allArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    & cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

    & cmake --install $env:CVC_BUILD_DIR
    if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
}

function ConvertTo-MsysPath {
    # Convert a Windows path (C:\foo\bar) to MSYS/Git-Bash style (/c/foo/bar)
    # so that autotools configure scripts invoked via `bash` work correctly.
    param([string]$Path)
    if ($Path -match '^([A-Za-z]):(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest = $Matches[2] -replace '\\','/'
        return "/$drive$rest"
    }
    return ($Path -replace '\\','/')
}

Write-Host "-- env-wasm.ps1 loaded --"
Write-Host "  EMSDK=$env:CVC_EMSDK_DIR  BUILD_TYPE=$cmakeBuildType  LINK=static  JOBS=$env:CVC_JOBS"
