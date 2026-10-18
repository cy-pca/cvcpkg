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

# -std=gnu17: MinGW gcc 15+ defaults to C23, where loop_wchar.h's old-style
# `extern size_t mbrtowc ();` declares a no-argument function ("too many
# arguments to function 'mbrtowc'"); the helper clears CFLAGS, so pass it to
# configure.
Invoke-CvcMsysAutotoolsBuild -ConfigureArgs @('--disable-dependency-tracking', "CFLAGS='-O2 -std=gnu17'")

# No libtool .la files in the bundle, as on Unix (build.sh): they record this
# job's prefix, which consumers' libtool then follows after relocation.
Get-ChildItem -Path $env:CVC_INSTALL_DIR -Recurse -Filter '*.la' -File | Remove-Item -Force

Invoke-CvcRewriteInstallPaths
