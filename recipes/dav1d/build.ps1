# recipes/dav1d/build.ps1 — build dav1d AV1 decoder on Windows via Meson + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild -MesonArgs @(
    '-Denable_tests=false',
    '-Denable_tools=false',
    '-Denable_examples=false'
)
