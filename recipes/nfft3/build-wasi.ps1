# recipes/nfft3/build-wasi.ps1 — cross-compile NFFT3 to wasm32-wasi via wasi-sdk on Windows.
#
# Uses the shared Invoke-CvcWasiAutotoolsBuild helper (see env-wasi.ps1).
# fftw3 is picked up from CVC_DEPS_PREFIX (populated by the fftw3 wasi
# build).  Threads/OpenMP are disabled — wasi-libc has no pthreads.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

$fftwPrefix = if ($env:CVC_DEPS_PREFIX -and (Test-Path (Join-Path $env:CVC_DEPS_PREFIX 'include'))) {
    ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX
} else {
    ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
}

Invoke-CvcWasiAutotoolsBuild -ConfigureArgs @(
    '--with-pic',
    '--disable-examples',
    '--disable-applications',
    '--disable-openmp',
    "--with-fftw3-includedir=$fftwPrefix/include",
    "--with-fftw3-libdir=$fftwPrefix/lib"
)
