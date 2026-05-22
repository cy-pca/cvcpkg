<#
.SYNOPSIS
  Build pthreads4w from vcpkg for Windows.
#>
param(
    [string]$Prefix  = $env:CVC_PREFIX,
    [string]$Triplet = "x64-windows"
)
$ErrorActionPreference = "Stop"

. "$PSScriptRoot/../_common/env-windows.ps1"

# Install via vcpkg (the release CI already carries vcpkg)
if (Get-Command vcpkg -ErrorAction SilentlyContinue) {
    vcpkg install "pthreads:$Triplet"
    $vcpkgRoot = & vcpkg env "echo %VCPKG_ROOT%" 2>$null
    if (-not $vcpkgRoot) { $vcpkgRoot = Split-Path (Get-Command vcpkg).Source }
    $installed = Join-Path $vcpkgRoot "installed/$Triplet"

    # Stage into prefix
    foreach ($sub in @("include","lib","bin","share")) {
        $src = Join-Path $installed $sub
        if (Test-Path $src) {
            Copy-Item -Recurse -Force $src $Prefix
        }
    }
} else {
    Write-Error "vcpkg not found – pthreads4w requires vcpkg on Windows."
    exit 1
}

Write-Host "pthreads4w installed to $Prefix"
