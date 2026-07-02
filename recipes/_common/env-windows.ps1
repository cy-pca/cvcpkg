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

# On Windows we ALWAYS build with MSVC (cl.exe).  A prior builder image
# happened to have Strawberry Perl's g++ ahead of cl on PATH, and the
# Ninja generator silently picked it, producing GNU-ar '.a' archives
# with Itanium-mangled symbols that MSVC-mangled downstream consumers
# (protobuf, grpc, etc.) cannot resolve — 100+ "unresolved external"
# link errors.  Force cl.exe here so the mangling and archive format
# stay consistent across the whole dep set.
$env:CC  = 'cl'
$env:CXX = 'cl'

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
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        '-DCMAKE_C_COMPILER=cl',
        '-DCMAKE_CXX_COMPILER=cl'
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

    # vcpkg defaults its download / tool cache to %LOCALAPPDATA%\vcpkg.
    # When the builder runs as the LocalSystem account, that resolves
    # to C:\Windows\System32\config\systemprofile\AppData\Local\vcpkg,
    # where the bundled 7zr.exe intermittently fails with
    # 'The system cannot find the path specified' while extracting
    # 7z2501.7z (long-path / SYSTEM-profile weirdness).  Redirect the
    # caches to a plain path under the builder's work dir and wipe any
    # partial 7z extraction so a fresh redownload is triggered.
    $vcpkgCache = Join-Path $env:CVC_BUILD_DIR 'vcpkg-cache'
    $env:VCPKG_DOWNLOADS       = Join-Path $vcpkgCache 'downloads'
    $env:VCPKG_DEFAULT_BINARY_CACHE = Join-Path $vcpkgCache 'archives'
    New-Item -ItemType Directory -Force -Path $env:VCPKG_DOWNLOADS       | Out-Null
    New-Item -ItemType Directory -Force -Path $env:VCPKG_DEFAULT_BINARY_CACHE | Out-Null
    foreach ($stale in @(
        (Join-Path $env:LOCALAPPDATA 'vcpkg\downloads\7z2501.7z'),
        (Join-Path $env:LOCALAPPDATA 'vcpkg\downloads\tools\7zr-25.01-windows')
    )) {
        if ($stale -and (Test-Path $stale)) {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $stale
        }
    }

    # Newer vcpkg distributions (e.g. the one bootstrapped on phm-win11)
    # ship without a "classic mode" instance and refuse
    #   vcpkg install <port>[:triplet]
    # with 'Could not locate a manifest (vcpkg.json) above the current
    # working directory'.  Generate an ephemeral manifest and drive vcpkg
    # in manifest mode instead — that works in both classic-capable and
    # manifest-only distributions.
    $manifestDir = Join-Path $env:CVC_BUILD_DIR ("vcpkg-manifest-" + $Port)
    New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
    $depEntry = if ($Features.Count -gt 0) {
        @{ name = $Port; features = @($Features) }
    } else {
        $Port
    }
    $manifest = @{
        name             = "cvcpkg-$($Port.ToLower())-stage"
        'version-string' = '0.0.0'
        dependencies     = @($depEntry)
    }
    # Newer vcpkg refuses manifest-mode operations without a
    # 'builtin-baseline' pinning the default registry to a git SHA
    # (error: "this vcpkg instance requires a manifest with a specified
    # baseline in order to interact with ports").  Try to derive the
    # baseline from the vcpkg checkout the vcpkg.exe on PATH belongs to.
    $vcpkgRoot = $env:VCPKG_ROOT
    if (-not $vcpkgRoot) {
        $vcpkgCmd = Get-Command vcpkg -ErrorAction SilentlyContinue
        if ($vcpkgCmd) {
            $vcpkgRoot = Split-Path -Parent $vcpkgCmd.Source
        }
    }
    if ($vcpkgRoot -and (Test-Path (Join-Path $vcpkgRoot '.git'))) {
        try {
            $baseline = (& git -C $vcpkgRoot rev-parse HEAD 2>$null)
            if ($baseline) {
                $manifest['builtin-baseline'] = $baseline.Trim()
            }
        } catch { }
    }
    $manifestPath = Join-Path $manifestDir 'vcpkg.json'
    ($manifest | ConvertTo-Json -Depth 6) | Set-Content -Encoding UTF8 $manifestPath

    $vcpkgArgs = @(
        'install',
        "--x-manifest-root=$manifestDir",
        "--x-install-root=$env:CVC_BUILD_DIR/vcpkg-installed",
        "--triplet=$Triplet"
    )
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
