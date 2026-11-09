# recipes/mujoco/build.ps1 — build MuJoCo (mujoco.dll + plugins + simulate)
# on Windows with CMake + Ninja + MSVC.  Mirrors build.sh; see that file for
# what is and is not built and why.
#
# Windows specifics:
#   * mujoco.dll goes to bin\, its import library mujoco.lib to lib\.
#   * Plugins (actuator.dll, sensor.dll, sdf_plugin.dll) go to
#     bin\mujoco_plugin\, beside simulate.exe, where simulate looks for them.
#   * GLFW is linked statically into simulate.exe / basic.exe (upstream's
#     MUJOCO_EXTRAS_STATIC_GLFW default), so no glfw3.dll is needed.
#   * upstream applies its qhull/libccd patches with `git apply`, so git.exe
#     must be on PATH (it is on every Windows builder and dev box).
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\..\_common\env-windows.ps1"
. "$PSScriptRoot\deps.ps1"

$deps = Invoke-MjFetchDeps

$log = Join-Path $env:CVC_BUILD_DIR 'mujoco-build.log'
New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null

# Invoke-CvcCMakeBuild writes cmake's output to the host; capture it so the
# no-git-fetch check below can read the configure messages.
Invoke-CvcCMakeBuild @(
    '-DMUJOCO_BUILD_EXAMPLES=ON',
    '-DMUJOCO_BUILD_SIMULATE=ON',
    '-DMUJOCO_BUILD_TESTS=OFF',
    '-DMUJOCO_TEST_PYTHON_UTIL=OFF',
    '-DMUJOCO_BUILD_STUDIO=OFF',
    '-DMUJOCO_USE_FILAMENT=OFF',
    '-DMUJOCO_WITH_USD=OFF',
    '-DMUJOCO_ENABLE_RPATH=OFF',
    "-DMUJOCO_CMAKE_DEP_CACHE=$($deps.Cache)",
    "-DFETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP=$($deps.MarchingCubeCpp)"
) *>&1 | Tee-Object -FilePath $log
Assert-MjNoGitFetch -Log $log

# ── plugins → bin\mujoco_plugin ────────────────────────────────────────────
$plugDir = Join-Path $env:CVC_INSTALL_DIR 'bin\mujoco_plugin'
New-Item -ItemType Directory -Force -Path $plugDir | Out-Null
$depsBuild = Join-Path $env:CVC_BUILD_DIR '_deps'
$plugins = @(Get-ChildItem -LiteralPath $env:CVC_BUILD_DIR -Recurse -File -Include 'actuator.dll', 'sensor.dll', 'sdf_plugin.dll' |
    Where-Object { -not $_.FullName.StartsWith($depsBuild, [System.StringComparison]::OrdinalIgnoreCase) } |
    Sort-Object -Property Name -Unique)
if ($plugins.Count -ne 3) {
    throw "mujoco: expected 3 plugin DLLs, found $($plugins.Count): $($plugins.FullName -join ', ')"
}
foreach ($p in $plugins) { Copy-Item -Force -LiteralPath $p.FullName -Destination $plugDir }
Write-Host "mujoco: installed $($plugins.Count) plugins into bin\mujoco_plugin"

# ── licence texts ──────────────────────────────────────────────────────────
Install-MjLicenses -Destination (Join-Path $env:CVC_INSTALL_DIR 'share\licenses\mujoco') -Cache $deps.Cache

Invoke-CvcRewriteInstallPaths
