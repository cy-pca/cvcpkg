# recipes/iconv/build.ps1 — build GNU libiconv on Windows via MSYS2 MinGW-w64.
#
# libiconv is autotools-only upstream; MSVC has no working port.  Use
# the same MSYS2/MinGW pipeline as gmp/mpfr/gsl — the helper installs
# the toolchain on demand if not already present, invokes ./configure
# with --host=x86_64-w64-mingw32, and post-processes libfoo.dll.a into
# foo.lib so MSVC downstream can link.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysAutotoolsBuild -ConfigureArgs @('--disable-dependency-tracking')

Invoke-CvcRewriteInstallPaths
