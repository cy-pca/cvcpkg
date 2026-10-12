# recipes/litecoin-core/build.ps1 — stage the official Litecoin Core release
# binaries on Windows (x86_64).
#
# Pure download/extract — no compiler needed, so this does NOT source
# env-windows.ps1.  The win64 zip and the source tarball (for the sample
# config, rpcauth.py and licence texts) are checked against the sha256 pinned
# below, copied from the release's clearsigned SHA256SUMS.asc whose signature
# and Gitian assertions were verified when the version was pinned.  Same
# staging as build.sh: the node and CLI tools, not litecoin-qt or
# test_litecoin.
$ErrorActionPreference = 'Stop'

$ver  = '0.21.5.8'
$base = "https://github.com/litecoin-project/litecoin/releases/download/v$ver"
$pins = @{
    "litecoin-$ver-win64.zip" = '78593091ef3545a2f06335d5f9ac5137a59e258d17e978951db693083b294109'
    "litecoin-$ver.tar.gz"    = '5c38653b1d45fd2d3975634ce9d44161512ce7afa6586424f707bdead14a772b'
}
$recipeDir = if ($env:CVC_RECIPE_DIR) { $env:CVC_RECIPE_DIR } else { $PSScriptRoot }

New-Item -ItemType Directory -Force $env:CVC_BUILD_DIR | Out-Null

function Get-Pinned([string]$name) {
    $dest = Join-Path $env:CVC_BUILD_DIR $name
    if (-not (Test-Path $dest)) {
        Write-Host "Downloading $base/$name ..."
        Invoke-WebRequest -Uri "$base/$name" -OutFile $dest -UseBasicParsing
    }
    $actual = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $pins[$name]) {
        throw "sha256 mismatch for ${name}: got $actual expected $($pins[$name])"
    }
    Write-Host "sha256 OK: $name"
    return $dest
}

$zip = Get-Pinned "litecoin-$ver-win64.zip"
$src = Get-Pinned "litecoin-$ver.tar.gz"

$x = Join-Path $env:CVC_BUILD_DIR 'extract'
if (Test-Path $x) { Remove-Item -Recurse -Force $x }
New-Item -ItemType Directory -Force $x | Out-Null
Expand-Archive -Path $zip -DestinationPath $x -Force
$s = Join-Path $x "litecoin-$ver"
if (-not (Test-Path (Join-Path $s 'bin\litecoind.exe'))) { throw 'unexpected archive layout' }

$i = $env:CVC_INSTALL_DIR
$dirs = @('bin', 'share\man\man1', 'etc\litecoin', 'share\litecoin-core\rpcauth',
          'share\licenses\litecoin-core', 'share\doc\litecoin-core')
foreach ($d in $dirs) { New-Item -ItemType Directory -Force (Join-Path $i $d) | Out-Null }

foreach ($t in @('litecoind', 'litecoin-cli', 'litecoin-tx', 'litecoin-wallet')) {
    Copy-Item (Join-Path $s "bin\$t.exe") (Join-Path $i "bin\$t.exe") -Force
    Copy-Item (Join-Path $s "share\man\man1\$t.1") (Join-Path $i "share\man\man1\$t.1") -Force
}
Copy-Item (Join-Path $s 'readme.txt') (Join-Path $i 'share\doc\litecoin-core\readme.txt') -Force

# Sample config, rpcauth.py and licence texts from the source tarball
# (Windows 10+ ships bsdtar as tar.exe).
$p = "litecoin-$ver"
$fromSrc = [ordered]@{
    'share/examples/litecoin.conf'     = 'etc\litecoin\litecoin.conf.sample'
    'share/rpcauth/rpcauth.py'         = 'share\litecoin-core\rpcauth\rpcauth.py'
    'share/rpcauth/README.md'          = 'share\litecoin-core\rpcauth\README.md'
    'COPYING'                          = 'share\licenses\litecoin-core\COPYING'
    'src/crc32c/LICENSE'               = 'share\licenses\litecoin-core\crc32c-LICENSE'
    'src/crypto/ctaes/COPYING'         = 'share\licenses\litecoin-core\ctaes-COPYING'
    'src/leveldb/LICENSE'              = 'share\licenses\litecoin-core\leveldb-LICENSE'
    'src/libmw/LICENSE'                = 'share\licenses\litecoin-core\libmw-LICENSE'
    'src/libmw/deps/caches/LICENSE.md' = 'share\licenses\litecoin-core\caches-LICENSE.md'
    'src/libmw/deps/ghc/LICENSE'       = 'share\licenses\litecoin-core\ghc-filesystem-LICENSE'
    'src/secp256k1-zkp/COPYING'        = 'share\licenses\litecoin-core\secp256k1-zkp-COPYING'
    'src/univalue/COPYING'             = 'share\licenses\litecoin-core\univalue-COPYING'
}
$members = @($fromSrc.Keys | ForEach-Object { "$p/$_" }) + @("$p/src/crypto/scrypt.cpp")
& tar.exe -xzf $src -C $x @members
if ($LASTEXITCODE -ne 0) { throw "tar failed extracting files from the source tarball" }
foreach ($k in $fromSrc.Keys) {
    Copy-Item (Join-Path $x "$p/$k") (Join-Path $i $fromSrc[$k]) -Force
}
# scrypt (BSD-2-Clause) carries its licence in the source header only.
$hdr = New-Object System.Collections.Generic.List[string]
foreach ($line in Get-Content (Join-Path $x "$p/src/crypto/scrypt.cpp")) {
    $hdr.Add($line)
    if ($line -match '\*/') { break }
}
if (-not ($hdr -match 'Colin Percival')) { throw 'scrypt licence header not found' }
Set-Content -Path (Join-Path $i 'share\licenses\litecoin-core\scrypt-LICENSE') -Value $hdr -Encoding ascii
Copy-Item (Join-Path $recipeDir 'THIRD-PARTY-NOTICES.txt') `
          (Join-Path $i 'share\licenses\litecoin-core\THIRD-PARTY-NOTICES.txt') -Force

& (Join-Path $i 'bin\litecoind.exe') -version
if ($LASTEXITCODE -ne 0) { throw 'staged litecoind.exe failed to run' }
