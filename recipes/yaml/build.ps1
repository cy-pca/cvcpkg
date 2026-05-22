# recipes/yaml/build.ps1 — build libyaml on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DYAML_BUILD_TESTING=OFF'
)
