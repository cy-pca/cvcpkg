# recipes/cairo/build.ps1 — build Cairo on Windows via Meson + MSVC.
#
# There is deliberately no "-Dwin32" here.  Cairo's meson build turns the
# win32 surface and win32 font backends on from a plain host check —
#
#     if host_machine.system() == 'windows'
#       feature_conf.set('CAIRO_HAS_WIN32_SURFACE', 1)
#       feature_conf.set('CAIRO_HAS_WIN32_FONT', 1)
#
# — so they are not switchable, and meson aborts setup on an unknown
# option name.  DirectWrite is the one win32 knob that IS an option
# (-Ddwrite, a feature defaulting to auto that probes for d2d1/dwrite);
# auto already resolves to enabled under MSVC because those libraries
# come from the Windows SDK, so we leave it unset rather than hard-
# requiring an option whose presence at this exact tag (1.18.2) we could
# not verify.
#
# Everything else mirrors build.sh so the Windows deliverable has the
# same shape as the POSIX one; only the X11 backends need turning off.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '-Dfreetype=enabled',
    '-Dfontconfig=enabled',
    '-Dpng=enabled',
    '-Dglib=enabled',
    '-Dtests=disabled',
    '-Dspectre=disabled',
    '-Dsymbol-lookup=disabled',
    '-Dxcb=disabled',
    '-Dxlib=disabled'
)
