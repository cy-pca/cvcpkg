# recipes/qt6/build.ps1 — obtain Qt 6 on Windows via aqtinstall.
# The Windows build uses pre-built Qt from aqtinstall (matching the
# existing release workflow) rather than building from source.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# On Windows, Qt is pre-installed by the CI via install-qt-action.
# This script stages the Qt installation into CVC_INSTALL_DIR.
Write-Host "Qt6 on Windows: staging from pre-installed Qt into prefix"
Write-Host "  (Full build-from-source support is a future enhancement)"

if ($env:Qt6_DIR) {
    $qtRoot = Split-Path -Parent (Split-Path -Parent $env:Qt6_DIR)
    Write-Host "  Staging from Qt6_DIR parent: $qtRoot"

    # Copy Qt files into the install prefix
    $dirs = @('bin', 'lib', 'include', 'plugins', 'share')
    foreach ($d in $dirs) {
        $src = Join-Path $qtRoot $d
        if (Test-Path $src) {
            Copy-Item -Recurse -Force $src $env:CVC_INSTALL_DIR
        }
    }
} else {
    Write-Host "  WARNING: Qt6_DIR not set; skipping Qt staging."
    Write-Host "  Set Qt6_DIR or install Qt via install-qt-action."
}
