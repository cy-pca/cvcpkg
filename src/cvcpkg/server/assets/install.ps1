# cvcpkg installer — https://cvcpkg.org/install.ps1
#
#   irm https://cvcpkg.org/install.ps1 | iex
#
# To pass options, create a script block from the download and call it:
#
#   & ([scriptblock]::Create((irm https://cvcpkg.org/install.ps1))) -InstallDir C:\tools\cvcpkg
#
# Installs the newest `cvcpkg` package that the cvcpkg registry serving this
# script has published for Windows (its bin\cvcpkg.exe), verified against the
# sha256 the registry recorded for it, for the current user (no admin rights
# required).  Each deployment bakes its own URL in when it serves this file,
# so a fork's /install.ps1 installs the fork's own builds.  Only when that
# registry has no cvcpkg for this machine (or for the pinned version) does it
# fall back to the standalone binary attached to the GitHub release, verified
# against the release's .sha256.
#
# Options:
#   -InstallDir DIR   install location (default: $env:LOCALAPPDATA\cvcpkg)
#   -Version VERSION  pin a version, e.g. 2.3.0 (a cvcpkg-v2.3.0 tag works too)
#   -Server URL       cvcpkg registry to install from
#
# Env overrides:
#   $env:CVCPKG_INSTALL_DIR  install location (default: $env:LOCALAPPDATA\cvcpkg)
#   $env:CVCPKG_VERSION      pin a version, as -Version
#   $env:CVCPKG_SERVER_URL   registry to install from, as -Server
#                            (default: the registry that served this script)
#   $env:CVCPKG_TOKEN        bearer token, for a registry that requires auth
#                            for reads; sent to that registry only
#   $env:CVCPKG_REPO         GitHub repo to fall back to (default: cy-pca/cvcpkg)
#
# Precedence: command-line flag > environment variable > built-in default.

param([string]$InstallDir, [string]$Version, [string]$Server)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 redraws Invoke-WebRequest's progress bar per chunk,
# which slows a download of this size by an order of magnitude.
$ProgressPreference = "SilentlyContinue"

# Baked in by the cvcpkg server that serves this script (its CVCPKG_SITE_URL
# and CVCPKG_GITHUB_REPO), so every deployment hands out an installer for its
# own builds.  As committed, these are the official deployment's.
$DefaultServerUrl = "https://cvcpkg.org"
$DefaultGitHubRepo = "cy-pca/cvcpkg"

$Repo = if ($env:CVCPKG_REPO) { $env:CVCPKG_REPO } else { $DefaultGitHubRepo }
$InstallDir = if ($InstallDir) { $InstallDir } elseif ($env:CVCPKG_INSTALL_DIR) { $env:CVCPKG_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "cvcpkg" }
$Server = if ($Server) { $Server } elseif ($env:CVCPKG_SERVER_URL) { $env:CVCPKG_SERVER_URL } else { $DefaultServerUrl }
$Server = $Server.TrimEnd("/")

# One pin serves both sources: the registry versions cvcpkg as 2.3.0 (plus a
# +cvc.N suffix for repackagings), GitHub tags the release cvcpkg-v2.3.0.
$Ver = if ($Version) { $Version } elseif ($env:CVCPKG_VERSION) { $env:CVCPKG_VERSION } else { "" }
$Ver = $Ver -replace '^cvcpkg-', '' -replace '^v', ''

function Die($msg) {
    Write-Error "error: $msg"
    exit 1
}

# A 32-bit PowerShell on 64-bit Windows reports x86 here; the real machine
# architecture is in PROCESSOR_ARCHITEW6432.
$MachineArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$Arch = switch ($MachineArch) {
    "AMD64" { "x86_64" }
    "ARM64" { "arm64" }
    default { $MachineArch.ToLower() }
}

# Sent to the registry only, so CVCPKG_TOKEN never reaches another host.
$RegistryHeaders = @{}
if ($env:CVCPKG_TOKEN) { $RegistryHeaders["Authorization"] = "Bearer $($env:CVCPKG_TOKEN)" }

# ---- Pick the source ----------------------------------------------------
# Only "nothing for this platform / version" (404), or a server too old to
# have the lookup at all (405: the path matches its DELETE route), falls back
# to GitHub.  Any other failure stops here: installing upstream's build
# because this registry was briefly unreachable is exactly what a fork does
# not want.
$Label = if ($Ver) { "cvcpkg $Ver" } else { "cvcpkg" }
Write-Host "Looking up $Label for windows/$Arch on $Server..."
$Query = "platform=windows&arch=$Arch&version=$([uri]::EscapeDataString($Ver))"
$Pkg = $null
$Status = 0
try {
    $Pkg = Invoke-RestMethod -Uri "$Server/v1/packages/cvcpkg/latest?$Query" -Headers $RegistryHeaders
} catch {
    if ($_.Exception.Response) { $Status = [int]$_.Exception.Response.StatusCode }
    $LookupError = $_.Exception.Message
}
if (-not $Pkg) {
    if ($Status -eq 401 -or $Status -eq 403) {
        Die "$Server requires authentication to read packages — set `$env:CVCPKG_TOKEN"
    } elseif ($Status -eq 405) {
        Write-Host "$Server is a cvcpkg server too old to publish cvcpkg itself; falling back to the GitHub releases of $Repo."
    } elseif ($Status -eq 404) {
        Write-Host "No $Label for windows/$Arch on $Server; falling back to the GitHub releases of $Repo."
    } else {
        Die "could not look up cvcpkg on $Server ($LookupError) — pass -Server (or set `$env:CVCPKG_SERVER_URL) to use another registry"
    }
    if ($Arch -ne "x86_64") {
        Die "no prebuilt cvcpkg for windows/$Arch on $Server or in the $Repo releases — try 'pip install cvcpkg' instead"
    }
    if ($Ver) {
        $Tag = "cvcpkg-v" + ($Ver -replace '\+.*$', '')
    } else {
        Write-Host "Resolving the latest cvcpkg release of $Repo..."
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" -UserAgent "cvcpkg-installer"
        $Tag = ($releases | Where-Object { $_.tag_name -match '^cvcpkg-v[0-9]' -and $_.tag_name -notmatch '-rc' } | Select-Object -First 1).tag_name
        if (-not $Tag) {
            Die "could not resolve the latest cvcpkg release — set `$env:CVCPKG_VERSION = 'X.Y.Z'"
        }
    }
}

$Tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "cvcpkg-install-$(Get-Random)")

try {
    if ($Pkg) {
        # ---- Source 1: the cvcpkg registry --------------------------------
        $Url = $Pkg.archive_url
        if (-not $Url -or -not $Pkg.sha256) {
            Die "unexpected answer from ${Server}: $($Pkg | ConvertTo-Json -Compress)"
        }
        if ($Url.StartsWith("/")) { $Url = "$Server$Url" }
        Write-Host "Installing cvcpkg $($Pkg.version) for windows/$Arch from $Server..."

        $Bundle = Join-Path $Tmp "bundle"
        $Headers = if ($Url.StartsWith("$Server/")) { $RegistryHeaders } else { @{} }
        Invoke-WebRequest -Uri $Url -OutFile $Bundle -Headers $Headers -UseBasicParsing
        $Expected = $Pkg.sha256.ToLower()
        $Actual = (Get-FileHash -Path $Bundle -Algorithm SHA256).Hash.ToLower()
        if ($Expected -ne $Actual) {
            Die "checksum mismatch for $Url (expected $Expected, got $Actual)"
        }

        # Sniffed rather than trusted to the name: registry bundles are all
        # called .tar.zst, and a Windows one is a zip.
        $Head = New-Object byte[] 2
        $Stream = [System.IO.File]::OpenRead($Bundle)
        try { [void]$Stream.Read($Head, 0, 2) } finally { $Stream.Close() }
        $PkgDir = Join-Path $Tmp "pkg"
        New-Item -ItemType Directory -Force -Path $PkgDir | Out-Null
        if ($Head[0] -eq 0x50 -and $Head[1] -eq 0x4B) {
            # Expand-Archive on Windows PowerShell 5.1 insists on a .zip name.
            $Zip = "$Bundle.zip"
            Move-Item -Path $Bundle -Destination $Zip
            Expand-Archive -Path $Zip -DestinationPath $PkgDir -Force
        } else {
            # Anything else is a tarball; Windows 10+ ships bsdtar as tar.exe.
            & tar.exe -xf $Bundle -C $PkgDir
            if ($LASTEXITCODE -ne 0) { Die "could not unpack $Url" }
        }
        $BinPath = Join-Path $PkgDir "bin\cvcpkg.exe"
        if (-not (Test-Path $BinPath)) {
            Die "the cvcpkg package at $Url has no bin\cvcpkg.exe"
        }
    } else {
        # ---- Source 2: the GitHub release (fallback) ----------------------
        $Asset = "cvcpkg-windows-x86_64.exe"
        Write-Host "Installing cvcpkg ($Tag) for windows/x86_64 from github.com/$Repo..."

        $BaseUrl = "https://github.com/$Repo/releases/download/$Tag"
        $BinPath = Join-Path $Tmp $Asset
        $ShaPath = "$BinPath.sha256"

        Invoke-WebRequest -Uri "$BaseUrl/$Asset" -OutFile $BinPath -UseBasicParsing
        Invoke-WebRequest -Uri "$BaseUrl/$Asset.sha256" -OutFile $ShaPath -UseBasicParsing

        $Expected = ((Get-Content $ShaPath) -split '\s+')[0].Trim().ToLower()
        $Actual = (Get-FileHash -Path $BinPath -Algorithm SHA256).Hash.ToLower()
        if ($Expected -ne $Actual) {
            Die "checksum mismatch for $Asset (expected $Expected, got $Actual)"
        }
    }

    # ---- Install ----------------------------------------------------------
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $DestPath = Join-Path $InstallDir "cvcpkg.exe"
    Copy-Item -Path $BinPath -Destination $DestPath -Force

    Write-Host "cvcpkg installed to $DestPath"

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$InstallDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", "User")
        $env:Path += ";$InstallDir"
        Write-Host "Added $InstallDir to your user PATH (restart your terminal for new sessions to pick it up)."
    }

    & $DestPath --version
}
finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}
