# recipes/proxy-libintl/build.ps1 — build proxy-libintl on Windows via Meson + MSVC.
#
# Upstream's meson.build compiles libintl.c with -DSTUB_ONLY, so the library
# never loads a real gettext: every lookup returns its msgid.  That matches
# GLib's Windows build, which is configured with -Dnls=disabled.
#
# Meson's dependency('intl') looks for libintl.h and intl.lib (it has no
# pkg-config method), so both link modes install lib\intl.lib.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild

$install = $env:CVC_INSTALL_DIR
if ($env:CVC_LINK -eq 'static') {
    # Meson names an MSVC static library libintl.a.
    Move-Item -Force "$install\lib\libintl.a" "$install\lib\intl.lib"
    # libintl.h declares its functions __declspec(dllimport) unless
    # G_INTL_STATIC_COMPILATION is defined; consumers of the static library
    # must not have to know that.
    $h = "$install\include\libintl.h"
    $text = Get-Content -Raw -LiteralPath $h
    Set-Content -NoNewline -Encoding ascii -LiteralPath $h -Value (
        "#ifndef G_INTL_STATIC_COMPILATION`n#define G_INTL_STATIC_COMPILATION 1`n#endif`n" + $text)
}

foreach ($f in @("$install\include\libintl.h", "$install\lib\intl.lib")) {
    if (-not (Test-Path -LiteralPath $f)) { throw "proxy-libintl: $f was not installed" }
}
