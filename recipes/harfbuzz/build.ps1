# recipes/harfbuzz/build.ps1 — build HarfBuzz on Windows via Meson + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '-Dglib=enabled',
    '-Dgobject=enabled',
    '-Dfreetype=enabled',
    '-Dcairo=disabled',
    '-Dicu=disabled',
    '-Dgraphite2=disabled',
    '-Dtests=disabled',
    '-Dintrospection=disabled',
    '-Ddocs=disabled',
    '-Dbenchmark=disabled'
)
