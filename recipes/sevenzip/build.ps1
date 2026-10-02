# recipes/sevenzip/build.ps1 — bootstrap a full 7z.exe + 7z.dll on Windows.
#
# The official Windows distribution is an installer, and the standalone 7za.exe
# cannot read .rar/.iso, so bootstrap the full codec DLL hermetically:
#   7zr.exe -> extract 7z2603-extra.7z -> 7za.exe (+ dlls)
#   7za.exe -> extract 7z2603-x64.exe  -> 7z.exe + 7z.dll (RAR/ISO capable)
# Each download is SHA-256 pinned.  Pure download/extract — no compiler, so this
# does NOT source env-windows.ps1.
$ErrorActionPreference = 'Stop'

$rel   = 'https://github.com/ip7z/7zip/releases/download/26.03'
$build = $env:CVC_BUILD_DIR; if (-not $build) { $build = [System.IO.Path]::GetTempPath() }
$bin   = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force $bin | Out-Null

$sha = @{
    '7zr.exe'         = 'ad4c82fadcbdf93c03b4fc440f300509c7d60c5c2f4d183e35d9d70d6957037d'
    '7z2603-extra.7z' = '191894e6acb3647ffb69ce630479ff318523b2e2b9890aa7f05c1127c2e59b8f'
    '7z2603-x64.exe'  = '0859c524b8a63551848f0c246abddcb1d0b7b656b0fbfe879f8d85e61a9e6edd'
}
function Get-File([string]$Url, [string]$Out, [string]$Expected) {
    Write-Host "Downloading $Url"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) { & $curl.Source -fSL --retry 8 -o $Out $Url; if ($LASTEXITCODE) { throw "curl failed $Url" } }
    else { Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing }
    $got = (Get-FileHash $Out -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $Expected) { throw "sha256 mismatch for $Out : $got != $Expected" }
}

$7zr   = Join-Path $build '7zr.exe'
$extra = Join-Path $build '7z2603-extra.7z'
$inst  = Join-Path $build '7z2603-x64.exe'
Get-File "$rel/7zr.exe"         $7zr   $sha['7zr.exe']
Get-File "$rel/7z2603-extra.7z" $extra $sha['7z2603-extra.7z']
Get-File "$rel/7z2603-x64.exe"  $inst  $sha['7z2603-x64.exe']

# 1) 7zr extracts the Extra package -> 7za.exe (prefer the x64 build).
$extraDir = Join-Path $build 'extra'
if (Test-Path $extraDir) { Remove-Item $extraDir -Recurse -Force }
& $7zr x -y "-o$extraDir" $extra | Out-Null
if ($LASTEXITCODE -ne 0) { throw "7zr failed to extract $extra" }
$7za = $null
foreach ($p in @('x64\7za.exe', '7za.exe')) { $c = Join-Path $extraDir $p; if (Test-Path $c) { $7za = $c; break } }
if (-not $7za) { throw "7za.exe not found in the Extra package" }
$srcDir = Split-Path -Parent $7za
foreach ($f in @('7za.exe', '7za.dll', '7zxa.dll')) {
    $s = Join-Path $srcDir $f
    if (Test-Path $s) { Copy-Item $s (Join-Path $bin $f) -Force }
}

# 2) 7za (or 7zr) extracts the installer -> full 7z.exe + 7z.dll (RAR/ISO).
$fullDir = Join-Path $build 'full'
if (Test-Path $fullDir) { Remove-Item $fullDir -Recurse -Force }
$got7z = $false
foreach ($tool in @($7za, $7zr)) {
    & $tool x -y "-o$fullDir" $inst 2>$null | Out-Null
    if ((Test-Path (Join-Path $fullDir '7z.exe')) -and (Test-Path (Join-Path $fullDir '7z.dll'))) { $got7z = $true; break }
}
if ($got7z) {
    Copy-Item (Join-Path $fullDir '7z.exe') (Join-Path $bin '7z.exe') -Force
    Copy-Item (Join-Path $fullDir '7z.dll') (Join-Path $bin '7z.dll') -Force
    Write-Host "Staged full 7z.exe + 7z.dll (RAR/ISO capable)."
}
else {
    Write-Warning "Could not extract full 7z.exe from the installer; only 7za.exe staged (no RAR)."
}
if (-not (Test-Path (Join-Path $bin '7za.exe'))) { throw "sevenzip bootstrap produced no console binary" }
Write-Host "sevenzip ready in $bin"
