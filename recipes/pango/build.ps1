# recipes/pango/build.ps1 — build Pango on Windows via Meson + MSVC.
#
# As with cairo, the win32 font backend is not an option to switch on:
# pango/meson.build wraps the whole libpangowin32 target in a bare
# `if host_system == 'windows'`, so it is built (and pangowin32.pc
# installed) automatically.  Pango 1.54's option set has no `win32`
# entry at all — passing one would abort meson setup.
#
# Xft is the one backend we must actively disable: it is X11-only.
# fontconfig and freetype stay enabled — upstream merely makes
# fontconfig *optional* on Windows rather than forbidding it, both are
# declared runtime deps of this recipe on every platform, and they are
# what keeps libpangoft2 in the deliverable.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '-Dintrospection=disabled',
    '-Dfontconfig=enabled',
    '-Dfreetype=enabled',
    '-Dcairo=enabled',
    '-Dxft=disabled',
    '-Dlibthai=disabled',
    '-Dsysprof=disabled',
    '-Dbuild-testsuite=false',
    '-Dbuild-examples=false'
)
