# recipes/nuklear/build.ps1 — install the single-header Nuklear GUI toolkit.
#
# Header-only: nothing to compile; output is identical for static/shared.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$includeDir = Join-Path $env:CVC_INSTALL_DIR 'include'
New-Item -ItemType Directory -Force -Path $includeDir | Out-Null
Copy-Item -Force (Join-Path $env:CVC_SOURCE_DIR 'nuklear.h') $includeDir

Invoke-CvcRewriteInstallPaths
