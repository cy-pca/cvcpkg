# recipes/imagemagick/build.ps1 — ImageMagick on Windows via vcpkg overlay port.
#
# ImageMagick uses its own VisualMagick build system on Windows, not CMake.
# The overlay port under vcpkg-overlay-ports/imagemagick/ (shipped
# inside the recipe archive) extracts headers, import libs and DLLs
# from the official upstream installer.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\\..\\_common\\env-windows.ps1"

$overlayDir = Join-Path $scriptDir 'vcpkg-overlay-ports'
if (-not (Test-Path $overlayDir)) {
    throw "Overlay ports directory not found: $overlayDir"
}

Invoke-CvcVcpkgInstall -Port 'imagemagick' -OverlayPorts $overlayDir
