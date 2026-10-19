# recipes/nfft3/build.ps1 — NFFT3 on Windows: autotools + MinGW-w64, all from cvcpkg.
#
# Was: the builder's own MSYS2 (C:\msys64), `pacman -S` of mingw gcc and FFTW
# during the build, a wget of the source, and that MSYS2's runtime DLLs copied
# into the bundle.  None of it came from cvcpkg, and on a builder whose MSYS2
# differed it stopped at configure ("C compiler cannot create executables",
# windows populate 2026-10-19).  Now: the recipe's own pinned source, cvcpkg's
# msys2 / make / mingw-w64-gcc / autotools (depends.build) through
# Invoke-CvcMsysAutotoolsBuild, and cvcpkg's FFTW (depends.runtime).  The
# helper builds shared + static and copies libnfft3.dll.a to nfft3.lib for
# MSVC consumers.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$fftw = ($env:CVC_DEPS_PREFIX -replace '\\', '/')

# No OpenMP: the bundle would have to ship MinGW's libgomp; the BSD builds go
# without it too (build.sh).  -static-libgcc keeps libgcc_s out of the DLL's
# imports.  CFLAGS as build.sh: left unset, configure picks -march=native,
# which ties the DLL to the builder's CPU.  The DLL links fftw3.dll through
# fftw3's GNU-named import library (lib/libfftw3.dll.a, fftw3 cvc.6); without
# it libtool drops -lfftw3 and builds nfft3 static-only.
Invoke-CvcMsysAutotoolsBuild -ConfigureArgs @(
    '--with-pic',
    '--disable-examples',
    '--disable-applications',
    '--disable-openmp',
    "--with-fftw3-includedir=$fftw/include",
    "--with-fftw3-libdir=$fftw/lib",
    "CFLAGS='-O3 -ffast-math'",  # one shell word: the helper joins args unquoted
    'LDFLAGS=-static-libgcc'
)

# libtool on a mingw host can still name the DLL cygnfft3-N.dll; give it the
# conventional libnfft3-N.dll name package.files and consumers expect.
$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
Get-ChildItem $bin -Filter 'cygnfft3*.dll' -ErrorAction SilentlyContinue | ForEach-Object {
    Rename-Item $_.FullName ('lib' + $_.Name.Substring(3))
}
if (-not (Get-ChildItem $bin -Filter 'libnfft3*.dll' -ErrorAction SilentlyContinue)) {
    throw "no libnfft3*.dll under $bin"
}

Invoke-CvcRewriteInstallPaths
