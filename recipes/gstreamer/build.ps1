# recipes/gstreamer/build.ps1 — build GStreamer on Windows via Meson + MSVC.
#
# Builds GStreamer core + base + good + bad; disables ugly/libav,
# bindings, docs, tests, and Linux-specific plugins.  The win32
# and directsound plugins are included automatically when available.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '--wrap-mode=nofallback',
    '-Dbase=enabled',
    '-Dgood=enabled',
    '-Dugly=disabled',
    '-Dbad=enabled',
    '-Dlibav=disabled',
    '-Ddevtools=disabled',
    '-Dges=disabled',
    '-Drtsp_server=disabled',
    '-Drs=disabled',
    '-Dvaapi=disabled',
    '-Dgst-examples=disabled',
    '-Dpython=disabled',
    '-Dsharp=disabled',
    '-Dtls=disabled',
    '-Dlibnice=disabled',
    '-Dqt5=disabled',
    '-Dqt6=disabled',
    '-Dwebrtc=disabled',
    '-Dintrospection=disabled',
    '-Dnls=disabled',
    '-Dorc=disabled',
    '-Ddoc=disabled',
    '-Dgtk_doc=disabled',
    '-Dtests=disabled',
    '-Dexamples=disabled',
    '-Dtools=enabled',
    '-Dgpl=disabled'
)
