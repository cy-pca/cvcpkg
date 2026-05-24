# recipes/imagemagick/build.ps1 — ImageMagick on Windows via vcpkg overlay port.
#
# ImageMagick uses its own VisualMagick build system on Windows, not CMake.
# The overlay port in vcpkg-overlay/ports/imagemagick extracts headers,
# import libs and DLLs from the official upstream installer.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\\..\\_common\\env-windows.ps1"

$repoRoot = (Resolve-Path "$scriptDir\\..\\.." ).Path
$overlayDir = Join-Path $repoRoot 'vcpkg-overlay' 'ports'

Invoke-CvcVcpkgInstall -Port 'imagemagick' -OverlayPorts $overlayDir
