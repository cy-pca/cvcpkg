# recipes/zig/build.ps1 — download + stage the official Zig release on Windows.
#
# Same layout as build.sh: bin\zig.exe beside a lib\zig\ standard library, so
# zig finds lib\zig\std\std.zig from its own location. Pure download, no
# compiler -- like the other prebuilt recipes it does not source
# env-windows.ps1.
$ErrorActionPreference = 'Stop'

# Keep in step with build.sh's ZIG_VER/sha_for() and recipe.yaml's
# source.artifacts -- three places, one release.
$ver = '0.15.2'
$dist = "zig-x86_64-windows-$ver"
$file = "$dist.zip"
$expected = '3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c'

$zip = Join-Path $env:CVC_BUILD_DIR $file
Write-Host "Downloading https://ziglang.org/download/$ver/$file ..."
Invoke-WebRequest -Uri "https://ziglang.org/download/$ver/$file" -OutFile $zip -UseBasicParsing

$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

# The zip's single top-level directory is $dist\. Windows' own tar.exe
# (bsdtar, since 1803) unpacks its ~20,000 files much faster than
# Expand-Archive. Named by full path: a bare `tar` can resolve to Git's GNU
# tar, which does not read zip files.
$bsdtar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (Test-Path $bsdtar) {
    & $bsdtar -xf $zip -C $env:CVC_BUILD_DIR
    if ($LASTEXITCODE -ne 0) { throw "zip extraction failed" }
} else {
    Expand-Archive -Path $zip -DestinationPath $env:CVC_BUILD_DIR -Force
}
Remove-Item -Force $zip
$src = Join-Path $env:CVC_BUILD_DIR $dist
if (-not (Test-Path (Join-Path $src 'lib\std\std.zig'))) { throw "unexpected archive layout (no lib\std\std.zig in $dist\)" }

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$doc = Join-Path $env:CVC_INSTALL_DIR 'share\doc\zig'
New-Item -ItemType Directory -Force -Path $bin, $lib, $doc | Out-Null
$libzig = Join-Path $lib 'zig'
if (Test-Path $libzig) { Remove-Item -Recurse -Force $libzig }
Move-Item (Join-Path $src 'lib') $libzig
Copy-Item (Join-Path $src 'zig.exe') $bin
foreach ($f in 'LICENSE', 'README.md', 'doc\langref.html') { Copy-Item (Join-Path $src $f) $doc }
Remove-Item -Recurse -Force $src

$zig = Join-Path $bin 'zig.exe'
& $zig version
if ($LASTEXITCODE -ne 0) { throw "staged zig.exe failed to run" }
& $zig env
if ($LASTEXITCODE -ne 0) { throw "staged zig.exe could not find its lib\zig" }
