# recipes/idn2/build.ps1 — build GNU libidn2 on Windows via MSYS2 MinGW-w64.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$extra = @(
    '--disable-dependency-tracking',
    '--disable-doc',
    '--disable-gtk-doc',
    '--disable-nls'
)
# Point at cvcpkg-provided libunistring / iconv when present.
if ($env:CVC_DEPS_PREFIX) {
    $msysDeps = ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX
    if (Test-Path (Join-Path $env:CVC_DEPS_PREFIX 'include\unistr.h')) {
        $extra += "--with-libunistring-prefix=$msysDeps"
    }
    if (Test-Path (Join-Path $env:CVC_DEPS_PREFIX 'include\iconv.h')) {
        $extra += "--with-libiconv-prefix=$msysDeps"
    }
}

Invoke-CvcMsysAutotoolsBuild -ConfigureArgs $extra

Invoke-CvcRewriteInstallPaths
