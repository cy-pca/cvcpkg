<#
.SYNOPSIS
  Build NFFT3 on Windows via MSYS2/mingw64 + generate MSVC import lib.
  Mirrors the CI logic in .github/workflows/release.yml.
.DESCRIPTION
  1) Builds NFFT3 with autotools inside MSYS2/mingw64.
  2) Uses gendef + MSVC lib /def: to create an MSVC-friendly .lib.
  3) Stages DLLs, headers, import lib, and pkg-config into $Prefix.
#>
param(
    [string]$Prefix = $env:CVC_PREFIX
)
$ErrorActionPreference = "Stop"

. "$PSScriptRoot/../_common/env-windows.ps1"

$NfftVersion = "3.5.3"
$NfftSha256  = "caf1b3b3e5bf8c33a6bfd7eca811d954efce896605ecfd0144d47d0bebdf4371"

# ── Ensure MSYS2 is available ──
$msys2 = "C:\msys64\usr\bin\bash.exe"
if (-not (Test-Path $msys2)) {
    Write-Error "MSYS2 not found at C:\msys64 – install MSYS2/mingw64 first."
    exit 1
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) "nfft-build-$([guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Force -Path $work | Out-Null

# ── Build inside MSYS2 (mingw64) ──
$buildScript = @"
set -ex
export MSYSTEM=MINGW64
export PATH="/mingw64/bin:/usr/bin:`$PATH"

# Install build deps if missing
pacman -S --noconfirm --needed \
    autoconf automake libtool perl make tar wget \
    mingw-w64-x86_64-gcc mingw-w64-x86_64-fftw \
    mingw-w64-x86_64-tools-git 2>/dev/null || true

cd "$($work -replace '\\','/')"

url_gh="https://github.com/NFFT/nfft/releases/download/$NfftVersion/nfft-$NfftVersion.tar.gz"
url_tuc="https://www-user.tu-chemnitz.de/~potts/nfft/download/nfft-$NfftVersion.tar.gz"
wget -q "`$url_gh" -O nfft.tar.gz || wget -q "`$url_tuc" -O nfft.tar.gz
echo "$NfftSha256  nfft.tar.gz" | sha256sum -c -
tar -xzf nfft.tar.gz
cd "nfft-$NfftVersion"

INSTALL_DIR="$($work -replace '\\','/')/install"
mkdir -p "`$INSTALL_DIR"

./configure \
    --prefix="`$INSTALL_DIR" \
    --enable-openmp \
    --enable-shared \
    --disable-static \
    --disable-examples \
    --disable-applications \
    --with-fftw3-includedir=/mingw64/include \
    --with-fftw3-libdir=/mingw64/lib \
    --with-gcc-arch=haswell \
    CFLAGS="-O3 -ffast-math" \
    LDFLAGS="-static-libgcc"
make -j`$(nproc)
make install
"@

$scriptPath = Join-Path $work "build_nfft.sh"
[System.IO.File]::WriteAllText($scriptPath, ($buildScript -replace "`r`n", "`n"))
& $msys2 --login -c "source `"$($scriptPath -replace '\\','/')`""
if ($LASTEXITCODE -ne 0) { throw "MSYS2 NFFT3 build failed ($LASTEXITCODE)" }

# ── Stage into prefix ──
$src = Join-Path $work "install"
New-Item -ItemType Directory -Force -Path "$Prefix/bin","$Prefix/lib","$Prefix/include","$Prefix/lib/pkgconfig" | Out-Null

# DLLs
$nfftDlls = Get-ChildItem "$src/bin" -Filter '*.dll' -ErrorAction SilentlyContinue
if (-not $nfftDlls) {
    $nfftDlls = Get-ChildItem "$src/lib" -Filter '*.dll' -ErrorAction SilentlyContinue
}
if (-not $nfftDlls) { throw "No NFFT DLLs found under $src" }
foreach ($f in $nfftDlls) { Copy-Item $f.FullName "$Prefix/bin/" -Force }

# Stage mingw runtime DLLs
$mingwBin = 'C:\msys64\mingw64\bin'
$runtimeDlls = @(
    'libfftw3-3.dll',
    'libfftw3_threads-3.dll',
    'libgcc_s_seh-1.dll',
    'libwinpthread-1.dll',
    'libgomp-1.dll',
    'libstdc++-6.dll'
)
foreach ($name in $runtimeDlls) {
    $p = Join-Path $mingwBin $name
    if (Test-Path $p) {
        Copy-Item $p "$Prefix/bin/" -Force
        Write-Host "Staged runtime dep: $name"
    }
}

# Headers
if (Test-Path "$src/include") {
    Copy-Item "$src/include/*.h" "$Prefix/include/" -Force -ErrorAction SilentlyContinue
}

# ── Generate MSVC import library ──
$targetDll = Get-ChildItem "$Prefix/bin" -Filter 'libnfft3_threads-*.dll' | Select-Object -First 1
if (-not $targetDll) {
    $targetDll = Get-ChildItem "$Prefix/bin" -Filter 'libnfft3-*.dll' | Select-Object -First 1
}
if (-not $targetDll) { throw "No NFFT DLL in $Prefix/bin after staging." }
Write-Host "Generating import lib from $($targetDll.Name)"

$impWork = Join-Path $work "implib"
New-Item -ItemType Directory -Force -Path $impWork | Out-Null
Copy-Item $targetDll.FullName $impWork -Force
Push-Location $impWork

$env:PATH = "C:\msys64\mingw64\bin;C:\msys64\usr\bin;$env:PATH"
& gendef $targetDll.Name
if ($LASTEXITCODE -ne 0) { throw "gendef failed ($LASTEXITCODE)" }
$defFile = [IO.Path]::ChangeExtension($targetDll.Name, '.def')
if (-not (Test-Path $defFile)) { $defFile = ($targetDll.BaseName + '.def') }
if (-not (Test-Path $defFile)) { throw "gendef produced no .def file" }

& lib "/def:$defFile" "/out:nfft3.lib" /machine:x64
if ($LASTEXITCODE -ne 0) { throw "lib /def failed ($LASTEXITCODE)" }
Copy-Item nfft3.lib "$Prefix/lib/" -Force
if (Test-Path nfft3.exp) { Copy-Item nfft3.exp "$Prefix/lib/" -Force }
Pop-Location

# ── pkg-config file ──
$pcContent = @"
prefix=`${pcfiledir}/../..
exec_prefix=`${prefix}
libdir=`${prefix}/lib
includedir=`${prefix}/include

Name: nfft3
Description: Nonequispaced FFT (MSYS2/mingw64 build, MSVC import lib)
Version: $NfftVersion
Libs: -L`${libdir} -lnfft3
Cflags: -I`${includedir}
"@
[System.IO.File]::WriteAllText(
    (Join-Path "$Prefix/lib/pkgconfig" "nfft3.pc"),
    ($pcContent -replace "`r`n", "`n") + "`n")

# Cleanup
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

Write-Host "NFFT3 $NfftVersion installed to $Prefix"
