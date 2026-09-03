# cvcpkg installer — https://cvcpkg.org/install.ps1
#
#   irm https://cvcpkg.org/install.ps1 | iex
#
# Downloads the latest cvcpkg standalone binary release, verifies its
# sha256 checksum, and installs it for the current user (no admin
# rights required).
#
# Env overrides:
#   $env:CVCPKG_VERSION      pin a release tag, e.g. cvcpkg-v2.0.0
#   $env:CVCPKG_INSTALL_DIR  install location (default: $env:LOCALAPPDATA\cvcpkg)

$ErrorActionPreference = "Stop"

$Repo = "transfix/libcvc-deps"
$InstallDir = if ($env:CVCPKG_INSTALL_DIR) { $env:CVCPKG_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "cvcpkg" }

function Die($msg) {
    Write-Error "error: $msg"
    exit 1
}

if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    Die "no prebuilt cvcpkg binary for Windows/$($env:PROCESSOR_ARCHITECTURE) yet — try 'pip install cvcpkg' instead"
}
$Asset = "cvcpkg-windows-x86_64.exe"

if ($env:CVCPKG_VERSION) {
    $Tag = $env:CVCPKG_VERSION
} else {
    Write-Host "Resolving the latest cvcpkg release..."
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" -UserAgent "cvcpkg-installer"
    $Tag = ($releases | Where-Object { $_.tag_name -match '^cvcpkg-v[0-9]' -and $_.tag_name -notmatch '-rc' } | Select-Object -First 1).tag_name
    if (-not $Tag) {
        Die "could not resolve the latest cvcpkg release — set `$env:CVCPKG_VERSION = 'cvcpkg-vX.Y.Z'"
    }
}

Write-Host "Installing cvcpkg ($Tag) for windows/x86_64..."

$BaseUrl = "https://github.com/$Repo/releases/download/$Tag"
$Tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "cvcpkg-install-$(Get-Random)")

try {
    $BinPath = Join-Path $Tmp $Asset
    $ShaPath = "$BinPath.sha256"

    Invoke-WebRequest -Uri "$BaseUrl/$Asset" -OutFile $BinPath -UseBasicParsing
    Invoke-WebRequest -Uri "$BaseUrl/$Asset.sha256" -OutFile $ShaPath -UseBasicParsing

    $Expected = ((Get-Content $ShaPath) -split '\s+')[0].Trim().ToLower()
    $Actual = (Get-FileHash -Path $BinPath -Algorithm SHA256).Hash.ToLower()
    if ($Expected -ne $Actual) {
        Die "checksum mismatch for $Asset (expected $Expected, got $Actual)"
    }

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
