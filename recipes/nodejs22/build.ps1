# recipes/nodejs22/build.ps1 — download + stage the official Node.js 22 binaries on Windows.
#
# The win-x64 zip is a flat directory: node.exe, npm.cmd / npx.cmd (plus the
# sh and .ps1 variants) and node_modules\npm. npm.cmd locates node.exe and
# node_modules\npm relative to its own directory (%~dp0), so the whole tree is
# staged as-is into bin\. Pure download, no compiler -- like the other prebuilt
# recipes it does not source env-windows.ps1.
$ErrorActionPreference = 'Stop'

# Keep $ver/$expected in step with build.sh's NODE_VER/sha_for() and with
# recipe.yaml's source.artifacts -- three places, one release.
$ver = '22.23.3'
$stem = "node-v$ver-win-x64"
$expected = '2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71'

$zip = Join-Path $env:CVC_BUILD_DIR "$stem.zip"
Write-Host "Downloading https://nodejs.org/dist/v$ver/$stem.zip ..."
Invoke-WebRequest -Uri "https://nodejs.org/dist/v$ver/$stem.zip" -OutFile $zip -UseBasicParsing

$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

Expand-Archive -Path $zip -DestinationPath $env:CVC_BUILD_DIR -Force
$src = Join-Path $env:CVC_BUILD_DIR $stem

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item -Recurse -Force (Join-Path $src '*') $bin

# corepack is not shipped (see the recipe notes): drop the module and its shims.
Remove-Item -Recurse -Force (Join-Path $bin 'node_modules\corepack') -ErrorAction SilentlyContinue
Get-ChildItem -Path $bin -Filter 'corepack*' | Remove-Item -Force
# Docs go where the POSIX build puts them.
$doc = Join-Path $env:CVC_INSTALL_DIR 'share\doc\node'
New-Item -ItemType Directory -Force -Path $doc | Out-Null
foreach ($f in 'LICENSE', 'README.md', 'CHANGELOG.md') {
    Move-Item -Force (Join-Path $bin $f) $doc
}

& (Join-Path $bin 'node.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged node.exe failed to run" }
& (Join-Path $bin 'npm.cmd') --version
if ($LASTEXITCODE -ne 0) { throw "staged npm.cmd failed to run" }
