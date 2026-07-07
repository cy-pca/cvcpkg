# recipes/pixman/build.ps1 — build pixman on Windows via Meson + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '-Dtests=disabled',
    '-Ddemos=disabled',
    '-Dgtk=disabled',
    '-Dlibpng=disabled'
)
