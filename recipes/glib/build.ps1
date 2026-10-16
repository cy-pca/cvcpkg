# recipes/glib/build.ps1 — build GLib on Windows via Meson + MSVC.
#
# GLib 2.x has good MSVC support via Meson.  We disable Linux-only
# features (SELinux, libmount, dtrace, sysprof) and introspection
# (requires Python GObject headers not available here).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# GLib's meson build requires the Python 'packaging' module in the interpreter
# meson runs on.  meson.cmd runs the build prefix's python.exe (python312), and
# packaging-cp312 (recipe.yaml) installs into its Lib\site-packages.

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
