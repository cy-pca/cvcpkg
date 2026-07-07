# recipes/graphene/build.ps1 — build Graphene from source on Windows via Meson + MSVC.
#
# Graphene is Meson-only upstream.  Requires glib (via CVC_DEPS_PREFIX).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# ── Ensure meson is available ────────────────────────────────────
$mesonCmd = Get-Command meson -ErrorAction SilentlyContinue
if (-not $mesonCmd) {
    Write-Host "cvcpkg: meson not found on PATH; installing via pip ..."
    & python3 -m pip install --disable-pip-version-check --quiet meson 2>$null
    if ($LASTEXITCODE -ne 0) {
        & python -m pip install --disable-pip-version-check --quiet meson
    }
    if ($LASTEXITCODE -ne 0) { throw "pip install meson failed" }
}

# ── Strip MSYS2 dirs from PATH to avoid MinGW header contamination ──
$origPath = $env:PATH
$env:PATH = ($env:PATH -split ';' |
    Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'

try {
    $defaultLib = if ($env:CVC_LINK -eq 'static') { 'static' } else { 'shared' }

    $pkgConfigPath = if ($env:CVC_DEPS_PREFIX) {
        "$env:CVC_DEPS_PREFIX\lib\pkgconfig"
    } else { '' }

    Set-Location $env:CVC_SOURCE_DIR

    $setupArgs = @(
        'setup', $env:CVC_BUILD_DIR,
        "--prefix=$env:CVC_INSTALL_DIR",
        '--buildtype=release',
        '--libdir=lib',
        "--default-library=$defaultLib",
        '-Dgobject_types=true',
        '-Dintrospection=disabled',
        '-Dtests=false',
        '-Dinstalled_tests=false'
    )
    if ($pkgConfigPath) {
        $setupArgs += "--pkg-config-path=$pkgConfigPath"
    }

    & meson @setupArgs
    if ($LASTEXITCODE -ne 0) { throw "meson setup failed" }

    & ninja -C $env:CVC_BUILD_DIR -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "ninja build failed" }

    & ninja -C $env:CVC_BUILD_DIR install
    if ($LASTEXITCODE -ne 0) { throw "ninja install failed" }
} finally {
    $env:PATH = $origPath
}

Invoke-CvcRewriteInstallPaths
