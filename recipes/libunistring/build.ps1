# recipes/libunistring/build.ps1 — build GNU libunistring on Windows via MSYS2 MinGW-w64.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# libunistring's configure looks for libiconv on non-glibc systems.
# On Windows MinGW-w64 that's satisfied by the cvcpkg-provided iconv
# bundle at CVC_DEPS_PREFIX (if installed) — --with-libiconv-prefix
# is a Gnulib idiom libunistring's configure recognises.
$extra = @('--disable-dependency-tracking')
if ($env:CVC_DEPS_PREFIX -and (Test-Path (Join-Path $env:CVC_DEPS_PREFIX 'include\iconv.h'))) {
    $msysDeps = ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX
    $extra += "--with-libiconv-prefix=$msysDeps"
}

Invoke-CvcMsysAutotoolsBuild -ConfigureArgs $extra

Invoke-CvcRewriteInstallPaths
