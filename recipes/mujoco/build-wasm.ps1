# recipes/mujoco/build-wasm.ps1 — cross-compile MuJoCo to wasm with Emscripten
# from a Windows host.  Mirrors build-wasm.sh (see it for what the bundle
# holds and why); wasm-mt when CVC_WASM_THREADS=1.
#
# env-wasm.ps1 does not inject -pthread the way env-wasm.sh does, so the
# threaded flags are set here.  MuJoCo's own MUJOCO_WASM_THREADS switch adds
# -pthread to its compile flags and the JS link; CFLAGS/CXXFLAGS/LDFLAGS make
# the third-party archives match.
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\..\_common\env-wasm.ps1"
. "$PSScriptRoot\deps.ps1"

$threads = if ($env:CVC_WASM_THREADS -eq '1') { 'ON' } else { 'OFF' }
if ($threads -eq 'ON') {
    $env:CFLAGS   = "-pthread $env:CFLAGS".Trim()
    $env:CXXFLAGS = "-pthread $env:CXXFLAGS".Trim()
    $env:LDFLAGS  = "-pthread $env:LDFLAGS".Trim()
}
Write-Host "mujoco: wasm build, MUJOCO_WASM_THREADS=$threads"

$deps = Invoke-MjFetchDeps
Install-MjTsc   # --emit-tsd (mujoco.d.ts) needs tsc; see deps.ps1
New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null
$log = Join-Path $env:CVC_BUILD_DIR 'mujoco-build.log'
$dist = Join-Path $env:CVC_SOURCE_DIR 'wasm\dist'
if (Test-Path -LiteralPath $dist) { Remove-Item -Recurse -Force -LiteralPath $dist }

Invoke-CvcWasmCMakeBuild -ExtraArgs @(
    '-DCMAKE_INSTALL_LIBDIR=lib',
    "-DMUJOCO_WASM_THREADS=$threads",
    '-DMUJOCO_BUILD_TESTS_WASM=OFF',
    '-DMUJOCO_BUILD_STUDIO=OFF',
    '-DMUJOCO_USE_FILAMENT=OFF',
    "-DMUJOCO_CMAKE_DEP_CACHE=$($deps.Cache)",
    "-DFETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP=$($deps.MarchingCubeCpp)"
) *>&1 | Tee-Object -FilePath $log
Assert-MjNoGitFetch -Log $log

# ── JS bindings ────────────────────────────────────────────────────────────
$js = Join-Path $env:CVC_INSTALL_DIR 'share\mujoco\wasm'
New-Item -ItemType Directory -Force -Path $js | Out-Null
foreach ($f in 'mujoco.js', 'mujoco.wasm', 'mujoco.d.ts') {
    $p = Join-Path $dist $f
    if (-not (Test-Path -LiteralPath $p)) { throw "mujoco: $p was not built" }
    Copy-Item -Force -LiteralPath $p -Destination $js
}
$map = Join-Path $dist 'mujoco.wasm.map'
if (Test-Path -LiteralPath $map) { Copy-Item -Force -LiteralPath $map -Destination $js }
Copy-Item -Force -LiteralPath (Join-Path $env:CVC_SOURCE_DIR 'wasm\README.md') -Destination (Join-Path $js 'README.md')

# ── static libraries, headers, CMake package ───────────────────────────────
function Find-MjOne([string]$Name) {
    $hits = @(Get-ChildItem -LiteralPath $env:CVC_BUILD_DIR -Recurse -File -Filter $Name)
    if ($hits.Count -ne 1) { throw "mujoco: expected one $Name, found $($hits.Count)" }
    $hits[0].FullName
}
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$libPriv = Join-Path $lib 'mujoco'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include\mujoco'
New-Item -ItemType Directory -Force -Path $libPriv, $inc | Out-Null
Copy-Item -Force -LiteralPath (Find-MjOne 'libmujoco.a') -Destination (Join-Path $lib 'libmujoco.a')
$depLibs = @('ccd', 'tinyxml2', 'qhullstatic_r', 'lodepng', 'tinyobjloader', 'miniz')
foreach ($d in $depLibs) {
    Copy-Item -Force -LiteralPath (Find-MjOne "lib$d.a") -Destination (Join-Path $libPriv "lib$d.a")
}
# mujoco::render_noop (optional stub mjr_* functions).
Copy-Item -Force -LiteralPath (Find-MjOne 'librender_noop.a') -Destination (Join-Path $libPriv 'librender_noop.a')
Copy-Item -Force -Path (Join-Path $env:CVC_SOURCE_DIR 'include\mujoco\*.h') -Destination $inc

$verLine = Select-String -LiteralPath (Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt') -Pattern '^\s*VERSION\s+([0-9][0-9.]*)\s*$' |
    Select-Object -First 1
if (-not $verLine) { throw 'mujoco: could not read the version from CMakeLists.txt' }
$ver = $verLine.Matches[0].Groups[1].Value
$cmk = Join-Path $lib 'cmake\mujoco'
New-Item -ItemType Directory -Force -Path $cmk | Out-Null
foreach ($t in 'mujocoConfig', 'mujocoConfigVersion') {
    $text = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "wasm\$t.cmake.in")
    $text = $text.Replace('@MUJOCO_VERSION@', $ver).Replace('@MUJOCO_WASM_THREADS@', $threads).Replace('@MUJOCO_DEP_LIBS@', ($depLibs -join ' '))
    # LF endings and no BOM, like the bash path writes.
    [System.IO.File]::WriteAllText((Join-Path $cmk "$t.cmake"), ($text -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

Install-MjLicenses -Destination (Join-Path $env:CVC_INSTALL_DIR 'share\licenses\mujoco') -Cache $deps.Cache
Invoke-CvcRewriteInstallPaths
