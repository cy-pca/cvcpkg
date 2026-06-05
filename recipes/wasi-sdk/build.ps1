# recipes/wasi-sdk/build.ps1 — download and stage the WASI SDK on Windows.
$ErrorActionPreference = 'Stop'

$wasiSdkVer = '33.0'
$wasiSdkTag = 'wasi-sdk-33'
$wasiSdkBase = "https://github.com/WebAssembly/wasi-sdk/releases/download/$wasiSdkTag"

# Detect host architecture.
$hostArch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x86_64' }

$tarball = "wasi-sdk-${wasiSdkVer}-${hostArch}-windows.tar.gz"
$url = "$wasiSdkBase/$tarball"
$downloadPath = Join-Path $env:CVC_BUILD_DIR $tarball

Write-Host "Downloading $url..."
Invoke-WebRequest -Uri $url -OutFile $downloadPath -UseBasicParsing

Write-Host "Extracting..."
tar xf $downloadPath -C $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed" }

# The tarball extracts to wasi-sdk-<ver>-<arch>-windows/
$extractedDir = Join-Path $env:CVC_BUILD_DIR "wasi-sdk-${wasiSdkVer}-${hostArch}-windows"
if (-not (Test-Path $extractedDir)) {
    # Fallback: find the first wasi-sdk-* directory
    $extractedDir = (Get-ChildItem -Path $env:CVC_BUILD_DIR -Directory -Filter 'wasi-sdk-*' | Select-Object -First 1).FullName
}

# Stage into install prefix.
Get-ChildItem -Path $extractedDir | ForEach-Object {
    if ($_.PSIsContainer) {
        Copy-Item -Path $_.FullName -Destination (Join-Path $env:CVC_INSTALL_DIR $_.Name) -Recurse -Force
    } else {
        Copy-Item -Path $_.FullName -Destination $env:CVC_INSTALL_DIR -Force
    }
}

# Verify the toolchain works.
$clangExe = Join-Path $env:CVC_INSTALL_DIR 'bin\clang.exe'
if (Test-Path $clangExe) {
    Write-Host "wasi-sdk $wasiSdkVer installed — toolchain check:"
    & $clangExe --version | Select-Object -First 1
} else {
    throw "clang.exe not found in $env:CVC_INSTALL_DIR\bin\"
}

Write-Host "wasi-sdk $wasiSdkVer staged to $env:CVC_INSTALL_DIR"
