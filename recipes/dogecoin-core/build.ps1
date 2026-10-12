# recipes/dogecoin-core/build.ps1 — stage the official Dogecoin Core release
# binaries on Windows (x86_64).
#
# Pure download/extract — no compiler needed, so this does NOT source
# env-windows.ps1.  The win64 zip is checked against the sha256 pinned below,
# copied from the release's clearsigned SHA256SUMS.asc whose signature and
# Gitian assertions were verified when the version was pinned; the sample
# config, rpcuser.py and licence texts come from the release commit on
# raw.githubusercontent.com, each pinned by sha256.  Same staging as
# build.sh: the node and CLI tools, not dogecoin-qt, test_dogecoin or
# libdogecoinconsensus.
$ErrorActionPreference = 'Stop'

$ver    = '1.14.9'
$base   = "https://github.com/dogecoin/dogecoin/releases/download/v$ver"
$commit = 'e0a1c157791544e818c901bd9341896965afbf9d'   # tag v1.14.9
$raw    = "https://raw.githubusercontent.com/dogecoin/dogecoin/$commit"
$recipeDir = if ($env:CVC_RECIPE_DIR) { $env:CVC_RECIPE_DIR } else { $PSScriptRoot }

New-Item -ItemType Directory -Force $env:CVC_BUILD_DIR | Out-Null

function Get-Pinned([string]$url, [string]$dest, [string]$sha) {
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    if (-not (Test-Path $dest)) {
        Write-Host "Downloading $url ..."
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    }
    $actual = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha) { throw "sha256 mismatch for ${url}: got $actual expected $sha" }
    Write-Host "sha256 OK: $url"
}

$zip = Join-Path $env:CVC_BUILD_DIR "dogecoin-$ver-win64.zip"
Get-Pinned "$base/dogecoin-$ver-win64.zip" $zip '45864cedc210e6d573c7efd5f6694a440147d9773c4a8851a99882a2727ad804'

$i = $env:CVC_INSTALL_DIR
# tree file at $commit -> (sha256, install path)
$treeFiles = [ordered]@{
    'COPYING'                               = @('ff692211a86e69de91712125c9d6015fa5b1d15125329f80c09a9b61ec464bf6', 'share\licenses\dogecoin-core\COPYING')
    'src/leveldb/LICENSE'                   = @('ccc19f1da0798ed666609b65a5b44dd8b3abe6fc08b9c0592eb76e82e174db19', 'share\licenses\dogecoin-core\leveldb-LICENSE')
    'src/crypto/ctaes/COPYING'              = @('705391f5c3000c942b63c028977a372a5fb12eb0abc6210b3d835115e2544a06', 'share\licenses\dogecoin-core\ctaes-COPYING')
    'src/secp256k1/COPYING'                 = @('a735999c7e5649df6fcda6fb06ab97435851c392b1b93494ae8725f37441632f', 'share\licenses\dogecoin-core\secp256k1-COPYING')
    'src/univalue/COPYING'                  = @('bb1d33d960a5ab422f23858845ec0ff28ff1a1f967643e74546eaf9b85acb3f3', 'share\licenses\dogecoin-core\univalue-COPYING')
    'contrib/debian/examples/dogecoin.conf' = @('591f0f724455c7398fcd36b9c50e1d0013dfffef1400c8bb954a46eca0818e9d', 'etc\dogecoin\dogecoin.conf.sample')
    'share/rpcuser/rpcuser.py'              = @('ccf5eeb42690c67c3a1d3fd4159ecd88dd1fb4e6b24e48668f278975352aa763', 'share\dogecoin-core\rpcuser\rpcuser.py')
    'share/rpcuser/README.md'               = @('55468058e947c66aeee8390c6be78fe659b0e2e6c1649c77912950851e4eb237', 'share\dogecoin-core\rpcuser\README.md')
}
$tree = Join-Path $env:CVC_BUILD_DIR 'tree'
foreach ($k in $treeFiles.Keys) {
    Get-Pinned "$raw/$k" (Join-Path $tree $k) $treeFiles[$k][0]
}
$scryptSrc = Join-Path $tree 'src/crypto/scrypt.cpp'
Get-Pinned "$raw/src/crypto/scrypt.cpp" $scryptSrc '3ecc40e8726ae8406b105071024e65ae4a8314c3b3b52dcdeb955c131ca2e733'

$x = Join-Path $env:CVC_BUILD_DIR 'extract'
if (Test-Path $x) { Remove-Item -Recurse -Force $x }
New-Item -ItemType Directory -Force $x | Out-Null
Expand-Archive -Path $zip -DestinationPath $x -Force
$s = Join-Path $x "dogecoin-$ver"
if (-not (Test-Path (Join-Path $s 'bin\dogecoind.exe'))) { throw 'unexpected archive layout' }

$dirs = @('bin', 'share\man\man1', 'etc\dogecoin', 'share\dogecoin-core\rpcuser',
          'share\licenses\dogecoin-core')
foreach ($d in $dirs) { New-Item -ItemType Directory -Force (Join-Path $i $d) | Out-Null }

foreach ($t in @('dogecoind', 'dogecoin-cli', 'dogecoin-tx')) {
    Copy-Item (Join-Path $s "bin\$t.exe") (Join-Path $i "bin\$t.exe") -Force
    Copy-Item (Join-Path $s "share\man\man1\$t.1") (Join-Path $i "share\man\man1\$t.1") -Force
}
foreach ($k in $treeFiles.Keys) {
    Copy-Item (Join-Path $tree $k) (Join-Path $i $treeFiles[$k][1]) -Force
}
# scrypt (BSD-2-Clause) carries its licence in the source header only.
$hdr = New-Object System.Collections.Generic.List[string]
foreach ($line in Get-Content $scryptSrc) {
    $hdr.Add($line)
    if ($line -match '\*/') { break }
}
if (-not ($hdr -match 'Colin Percival')) { throw 'scrypt licence header not found' }
Set-Content -Path (Join-Path $i 'share\licenses\dogecoin-core\scrypt-LICENSE') -Value $hdr -Encoding ascii
Copy-Item (Join-Path $recipeDir 'THIRD-PARTY-NOTICES.txt') `
          (Join-Path $i 'share\licenses\dogecoin-core\THIRD-PARTY-NOTICES.txt') -Force

& (Join-Path $i 'bin\dogecoind.exe') -version
if ($LASTEXITCODE -ne 0) { throw 'staged dogecoind.exe failed to run' }
