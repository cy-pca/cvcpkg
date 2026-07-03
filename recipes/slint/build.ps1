# recipes/slint/build.ps1 — build Slint C++ bindings on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$slintVer = '1.13.0'
$tarball  = "v$slintVer.tar.gz"
$url      = "https://github.com/slint-ui/slint/archive/refs/tags/$tarball"

$src = $env:CVC_SOURCE_DIR
if (-not (Test-Path (Join-Path $src 'CMakeLists.txt'))) {
    $dl = Join-Path $env:CVC_BUILD_DIR $tarball
    Invoke-WebRequest -Uri $url -OutFile $dl -UseBasicParsing
    if (-not (Test-Path $src)) { New-Item -ItemType Directory -Path $src | Out-Null }
    tar xf $dl -C $src --strip-components=1
}

# Ensure rustup + cargo are present.
$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargo) {
    Write-Host "cargo not found; installing rustup into build tree ..."
    $env:CARGO_HOME  = Join-Path $env:CVC_BUILD_DIR 'cargo'
    $env:RUSTUP_HOME = Join-Path $env:CVC_BUILD_DIR 'rustup'
    $rustupInit = Join-Path $env:CVC_BUILD_DIR 'rustup-init.exe'
    Invoke-WebRequest -Uri 'https://win.rustup.rs/x86_64' -OutFile $rustupInit -UseBasicParsing
    & $rustupInit -y --profile minimal --default-toolchain stable
    $env:PATH = "$env:CARGO_HOME\bin;$env:PATH"
}

Invoke-CvcCMakeBuild @(
    '-DSLINT_BUILD_EXAMPLES=OFF',
    '-DSLINT_BUILD_TESTING=OFF',
    '-DSLINT_FEATURE_COMPILER=ON'
)
