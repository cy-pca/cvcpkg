# recipes/bazel/build.ps1 — install Bazelisk (as bazel.exe) on Windows.
$ErrorActionPreference = 'Stop'

$bazeliskVer = '1.22.1'
$base = "https://github.com/bazelbuild/bazelisk/releases/download/v$bazeliskVer"

$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
$binary = "bazelisk-windows-$arch.exe"
$url = "$base/$binary"

$binDir = Join-Path $env:CVC_INSTALL_DIR 'bin'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

$dest = Join-Path $binDir 'bazelisk.exe'
Write-Host "Downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing

# Windows can't symlink without privilege, so copy to bazel.exe.
Copy-Item -Path $dest -Destination (Join-Path $binDir 'bazel.exe') -Force

Write-Host "bazelisk $bazeliskVer installed:"
& $dest version
