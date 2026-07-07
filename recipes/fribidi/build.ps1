# recipes/fribidi/build.ps1 — build GNU FriBidi on Windows via Meson + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @('-Dtests=false', '-Ddocs=false')
