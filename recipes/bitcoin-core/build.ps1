# recipes/bitcoin-core/build.ps1 — stage the official Bitcoin Core release
# binaries on Windows (x86_64).
#
# Pure download/extract — no compiler needed, so this does NOT source
# env-windows.ps1.  The win64 zip and the source tarball (for the licence
# texts) are checked against the sha256 pinned below, copied from the
# release's SHA256SUMS whose guix-builder signatures were verified when the
# version was pinned.  Same staging as build.sh: the node and CLI tools, not
# bitcoin-qt or test_bitcoin.
$ErrorActionPreference = 'Stop'

$ver  = '31.1'
$base = "https://bitcoincore.org/bin/bitcoin-core-$ver"
$pins = @{
    "bitcoin-$ver-win64.zip" = 'c99ef173471c58e6766d9eebd12e6c35349082eeed3939bc99eed58ef57db587'
    "bitcoin-$ver.tar.gz"    = '50411d5b43c7e4c90099394759eb6c2add6e7c2dbe728840893d638b6fc6afc9'
}

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

$zip = Get-Pinned "bitcoin-$ver-win64.zip"
$src = Get-Pinned "bitcoin-$ver.tar.gz"

$x = Join-Path $env:CVC_BUILD_DIR 'extract'
if (Test-Path $x) { Remove-Item -Recurse -Force $x }
New-Item -ItemType Directory -Force $x | Out-Null
Expand-Archive -Path $zip -DestinationPath $x -Force
$s = Join-Path $x "bitcoin-$ver"
if (-not (Test-Path (Join-Path $s 'bin\bitcoind.exe'))) { throw 'unexpected archive layout' }

$i = $env:CVC_INSTALL_DIR
$dirs = @('bin', 'share\man\man1', 'etc\bitcoin', 'share\bitcoin-core\rpcauth',
          'share\licenses\bitcoin-core', 'share\doc\bitcoin-core')
foreach ($d in $dirs) { New-Item -ItemType Directory -Force (Join-Path $i $d) | Out-Null }

foreach ($t in @('bitcoind', 'bitcoin-cli', 'bitcoin-tx', 'bitcoin-util', 'bitcoin-wallet', 'bitcoin')) {
    Copy-Item (Join-Path $s "bin\$t.exe") (Join-Path $i "bin\$t.exe") -Force
    $man = Join-Path $s "share\man\man1\$t.1"
    if (Test-Path $man) { Copy-Item $man (Join-Path $i "share\man\man1\$t.1") -Force }
}
Copy-Item (Join-Path $s 'bitcoin.conf') (Join-Path $i 'etc\bitcoin\bitcoin.conf.sample') -Force
Copy-Item (Join-Path $s 'share\rpcauth\rpcauth.py') (Join-Path $i 'share\bitcoin-core\rpcauth\') -Force
Copy-Item (Join-Path $s 'share\rpcauth\README.md') (Join-Path $i 'share\bitcoin-core\rpcauth\') -Force
Copy-Item (Join-Path $s 'readme.txt') (Join-Path $i 'share\doc\bitcoin-core\readme.txt') -Force

# Licence texts from the source tarball (Windows 10+ ships bsdtar as tar.exe).
$licences = [ordered]@{
    'COPYING'                 = 'COPYING'
    'src/crc32c/LICENSE'      = 'crc32c-LICENSE'
    'src/crypto/ctaes/COPYING' = 'ctaes-COPYING'
    'src/leveldb/LICENSE'     = 'leveldb-LICENSE'
    'src/minisketch/LICENSE'  = 'minisketch-LICENSE'
    'src/secp256k1/COPYING'   = 'secp256k1-COPYING'
}
$members = @($licences.Keys | ForEach-Object { "bitcoin-$ver/$_" })
& tar.exe -xzf $src -C $x @members
if ($LASTEXITCODE -ne 0) { throw "tar failed extracting licence files" }
foreach ($k in $licences.Keys) {
    Copy-Item (Join-Path $x "bitcoin-$ver/$k") (Join-Path $i "share\licenses\bitcoin-core\$($licences[$k])") -Force
}

& (Join-Path $i 'bin\bitcoind.exe') -version
if ($LASTEXITCODE -ne 0) { throw 'staged bitcoind.exe failed to run' }
