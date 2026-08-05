# recipes/rust/build.ps1 — download + stage the official Rust toolchain on Windows.
#
# The x86_64-pc-windows-msvc release ships the same POSIX install.sh as the
# other platforms. This is a pure download (no compiler), so — like the other
# prebuilt recipes (wasi-sdk) — it does NOT source env-windows.ps1. We locate
# Git-Bash directly and run install.sh under it to stage rustc/cargo/std.
$ErrorActionPreference = 'Stop'

# Keep $ver/$expected in step with build.sh's RUST_VER/sha_for() and with
# recipe.yaml's source.artifacts — three places, one release.
$ver = '1.90.0'
$triple = 'x86_64-pc-windows-msvc'
$expected = '1aa997bcda4258795ea9eee1430843928dc185fad40067b180593456057a9126'

$tarball = "rust-$ver-$triple.tar.gz"
$archive = Join-Path $env:CVC_BUILD_DIR $tarball
Write-Host "Downloading https://static.rust-lang.org/dist/$tarball ..."
Invoke-WebRequest -Uri "https://static.rust-lang.org/dist/$tarball" -OutFile $archive -UseBasicParsing

$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "sha256 mismatch: got $actual expected $expected" }

# Extract with the built-in Windows tar.
tar xf $archive -C $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed" }

# Locate Git-Bash (git is a required host tool on Windows build machines).
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) { throw "git not found — needed for Git-Bash to run rust's install.sh" }
$gitRoot = Split-Path (Split-Path $gitCmd.Source)   # ...\Git\cmd\git.exe -> ...\Git
$bash = @(
    (Join-Path $gitRoot 'bin\bash.exe'),
    (Join-Path $gitRoot 'usr\bin\bash.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $bash) { throw "bash.exe not found under $gitRoot" }

# Convert Windows paths to MSYS form (C:\x -> /c/x) for install.sh.
function ConvertTo-Msys([string]$p) {
    $p = $p -replace '\\', '/'
    if ($p -match '^([A-Za-z]):(.*)$') { return "/$($Matches[1].ToLower())$($Matches[2])" }
    return $p
}
$msysBuild  = ConvertTo-Msys $env:CVC_BUILD_DIR
$msysPrefix = ConvertTo-Msys $env:CVC_INSTALL_DIR

$cmd = "cd '$msysBuild' && './rust-$ver-$triple/install.sh' --prefix='$msysPrefix' --without=rust-docs --disable-ldconfig"
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw "rust install.sh failed ($LASTEXITCODE)" }

& (Join-Path $env:CVC_INSTALL_DIR 'bin\rustc.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged rustc failed to run" }
& (Join-Path $env:CVC_INSTALL_DIR 'bin\cargo.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged cargo failed to run" }
