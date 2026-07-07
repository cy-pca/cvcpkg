# recipes/libepoxy/build.ps1 — build libepoxy on Windows via Meson + MSVC.
#
# On Windows, libepoxy uses the WGL backend (no EGL/GLX).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    '-Degl=no',
    '-Dglx=no',
    '-Dx11=false',
    '-Ddocs=false'
)
