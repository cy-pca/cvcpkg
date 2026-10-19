# recipes/fftw3/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTS=OFF',
    '-DENABLE_THREADS=ON',
    '-DWITH_COMBINED_THREADS=ON'
)

# GNU name for the import library, so MinGW/libtool consumers (nfft3) can link
# the DLL: libtool resolves -lfftw3 only through a libfftw3* file and otherwise
# drops the dependency and builds the consumer static-only.  Same bytes as
# fftw3.lib; only the import library may be aliased this way (see
# recipes/zlib/build.ps1 for why an MSVC static library must not be).
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$imp = Join-Path $lib 'fftw3.lib'
if (-not (Test-Path $imp)) { throw "fftw3: no import library at $imp" }
Copy-Item $imp (Join-Path $lib 'libfftw3.dll.a') -Force
