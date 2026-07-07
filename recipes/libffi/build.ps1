# recipes/libffi/build.ps1 — build libffi on Windows via MSYS2 + MinGW autotools.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysAutotoolsBuild -ConfigureArgs @(
    '--disable-docs',
    "--includedir=$(ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR)/include"
)

Invoke-CvcRewriteInstallPaths
