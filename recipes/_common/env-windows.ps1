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

# MSVC runtime library: static link -> /MT (static CRT), shared -> /MD (dynamic CRT)
# The Debug infix goes BEFORE DLL: MultiThreaded[Debug][DLL]
$msvcRuntime = if ($env:CVC_LINK -eq 'static') {
    'MultiThreaded$<$<CONFIG:Debug>:Debug>'
} else {
    'MultiThreaded$<$<CONFIG:Debug>:Debug>DLL'
}

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
        "-DBUILD_SHARED_LIBS=$buildSharedLibs",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
    ) + $ExtraArgs

    & cmake @allArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    & cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

    & cmake --install $env:CVC_BUILD_DIR
    if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
}

function Invoke-CvcVcpkgInstall {
    <#
    .SYNOPSIS
      Install a vcpkg port and stage its files into the cvcpkg prefix.
    .PARAMETER Port
      The vcpkg port name (e.g. "clapack", "pthreads").
    .PARAMETER Triplet
      vcpkg triplet. Defaults to x64-windows or x64-windows-static based on CVC_LINK.
    .PARAMETER Features
      Optional feature list (e.g. @("cpp")).
    .PARAMETER OverlayPorts
      Optional path to an overlay-ports directory (passed as --overlay-ports to vcpkg).
    #>
    param(
        [Parameter(Mandatory)][string]$Port,
        [string]$Triplet = '',
        [string[]]$Features = @(),
        [string]$OverlayPorts = ''
    )

    if (-not $Triplet) {
        $Triplet = if ($env:CVC_LINK -eq 'static') { 'x64-windows-static' } else { 'x64-windows' }
    }

    if (-not (Get-Command vcpkg -ErrorAction SilentlyContinue)) {
        throw "vcpkg not found on PATH — required for port '$Port'"
    }

    $spec = if ($Features.Count -gt 0) {
        "${Port}[$($Features -join ',')]:${Triplet}"
    } else {
        "${Port}:${Triplet}"
    }

    Write-Host "cvcpkg: vcpkg install $spec"
    $vcpkgArgs = @('install', $spec, "--x-install-root=$env:CVC_BUILD_DIR/vcpkg-installed")
    if ($OverlayPorts) {
        $vcpkgArgs += "--overlay-ports=$OverlayPorts"
    }
    & vcpkg @vcpkgArgs
    if ($LASTEXITCODE -ne 0) { throw "vcpkg install $spec failed" }

    $installed = Join-Path $env:CVC_BUILD_DIR "vcpkg-installed/$Triplet"
    foreach ($sub in @('include','lib','bin','share','tools')) {
        $src = Join-Path $installed $sub
        if (Test-Path $src) {
            Copy-Item -Recurse -Force $src $env:CVC_INSTALL_DIR
        }
    }
    # Always stage debug/ subdirectory — vcpkg cmake targets reference both
    # release and debug lib paths (e.g. ${_IMPORT_PREFIX}/debug/lib/foo.lib).
    $debugDir = Join-Path $installed "debug"
    if (Test-Path $debugDir) {
        $destDebug = Join-Path $env:CVC_INSTALL_DIR "debug"
        if (-not (Test-Path $destDebug)) { New-Item -ItemType Directory -Path $destDebug | Out-Null }
        foreach ($sub in @('lib','bin')) {
            $src = Join-Path $debugDir $sub
            if (Test-Path $src) {
                Copy-Item -Recurse -Force $src $destDebug
            }
        }
    }
    Write-Host "cvcpkg: $Port staged to $env:CVC_INSTALL_DIR"
}

Write-Host "-- env-windows.ps1 loaded --"
Write-Host "  BUILD_TYPE=$cmakeBuildType  LINK=$env:CVC_LINK  JOBS=$env:CVC_JOBS"
