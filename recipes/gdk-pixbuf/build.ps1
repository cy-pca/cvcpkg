# recipes/gdk-pixbuf/build.ps1 — build GDK-Pixbuf on Windows via Meson + MSVC.
#
# Two options exist only on Windows and both matter here:
#
#   -Dnative_windows_loaders=false  keeps the png/jpeg/tiff loaders pointed at
#       the cvcpkg libpng / libjpeg-turbo / tiff we declare as runtime deps,
#       instead of swapping in the WIC-backed ones.  It is already the upstream
#       default, but it is stated explicitly because flipping it would quietly
#       turn three declared runtime deps into dead weight and change what the
#       bundle can decode.
#   -Drelocatable=true  makes gdk-pixbuf resolve its loader/module paths from
#       the loaded module's own location rather than the prefix baked in at
#       build time.  A cvcpkg bundle is unpacked wherever the consumer wants
#       it, so the baked-in path is never right.
#
# The rest mirrors build.sh, plus -Dtests=false (the test suite is dead weight
# on the builder fleet — same call glib's build.ps1 makes).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '-Dpng=enabled',
    '-Djpeg=enabled',
    '-Dtiff=enabled',
    '-Dbuiltin_loaders=all',
    '-Dnative_windows_loaders=false',
    '-Drelocatable=true',
    '-Dtests=false',
    '-Dinstalled_tests=false',
    '-Dman=false',
    '-Dintrospection=disabled',
    '-Dgio_sniffing=false'
)
