# recipes/glib/build.ps1 — build GLib on Windows via Meson + MSVC.
#
# GLib 2.x has good MSVC support via Meson.  We disable Linux-only
# features (SELinux, libmount, dtrace, sysprof) and introspection
# (requires Python GObject headers not available here).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# GLib's meson build requires the Python 'packaging' module.
& python -m pip install --quiet packaging 2>$null

Invoke-CvcMesonBuild @(
    '-Dtests=false',
    '-Dglib_debug=disabled',
    '-Dnls=disabled',
    '-Dman-pages=disabled',
    '-Dselinux=disabled',
    '-Dlibmount=disabled',
    '-Dintrospection=disabled',
    '-Ddtrace=false',
    '-Dsysprof=disabled'
)
