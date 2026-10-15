# recipes/libigl/build.ps1 — install libigl's permissive core (header-only) from a
# Windows host: natively with MSVC (platform windows) and, through build-wasm.ps1,
# for wasm with Emscripten.  Same switches, the same six upstream work-arounds
# and the same post-install assertions as build.sh (see there for the why).
#
# The test shell (test.sh) gets neither the MSVC environment nor an activated
# emsdk, so smoke/ is configured, built and run HERE, while they are active.
#
# MSVC note: igl_add_library adds INTERFACE /MP, /bigobj and NOMINMAX when MSVC,
# so the Windows bundle's exported igl::core differs from the POSIX ones.
$ErrorActionPreference = 'Stop'

# Captured before dot-sourcing: env-wasm.ps1 reassigns $scriptDir.
$recipeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$isWasm = $env:CVC_PLATFORM -eq 'wasm'
if ($isWasm) {
    . "$recipeDir\..\_common\env-wasm.ps1"
} else {
    . "$recipeDir\..\_common\env-windows.ps1"   # imports vcvars64 if cl.exe is not on PATH
}

New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null
$pre = Join-Path $env:CVC_BUILD_DIR 'cvcpkg-find-eigen3.cmake'
Set-Content -LiteralPath $pre -Value 'find_package(Eigen3 CONFIG REQUIRED)' -Encoding Ascii

$backend = if ($isWasm) { 'SERIAL' } else { 'POOL' }
$iglArgs = @(
    "-DCMAKE_PROJECT_libigl_INCLUDE=$($pre -replace '\\', '/')",
    '-DCMAKE_INSTALL_LIBDIR=lib',
    '-DFETCHCONTENT_FULLY_DISCONNECTED=ON',
    '-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF',
    '-DCMAKE_DISABLE_FIND_PACKAGE_Matlab=ON',
    '-DCMAKE_DISABLE_FIND_PACKAGE_MOSEK=ON',
    '-DCMAKE_DISABLE_FIND_PACKAGE_BLAS=ON',
    '-DHUNTER_ENABLED=OFF',
    '-Dmodule_export=core',
    '-DLIBIGL_INSTALL=ON',
    '-DLIBIGL_USE_STATIC_LIBRARY=OFF',
    "-DLIBIGL_PARALLEL_FOR_BACKEND=$backend",
    '-DLIBIGL_BUILD_TESTS=OFF',
    '-DLIBIGL_BUILD_TUTORIALS=OFF',
    '-DLIBIGL_GLFW_TESTS=OFF',
    '-DLIBIGL_WARNINGS_AS_ERRORS=OFF',
    '-DLIBIGL_CYCODEBASE=OFF',
    '-DLIBIGL_EMBREE=OFF',
    '-DLIBIGL_GLFW=OFF',
    '-DLIBIGL_IMGUI=OFF',
    '-DLIBIGL_OPENGL=OFF',
    '-DLIBIGL_STB=OFF',
    '-DLIBIGL_PREDICATES=OFF',
    '-DLIBIGL_SPECTRA=OFF',
    '-DLIBIGL_XML=OFF',
    '-DLIBIGL_COPYLEFT_CORE=OFF',
    '-DLIBIGL_COPYLEFT_CGAL=OFF',
    '-DLIBIGL_COPYLEFT_COMISO=OFF',
    '-DLIBIGL_COPYLEFT_TETGEN=OFF',
    '-DLIBIGL_RESTRICTED_MATLAB=OFF',
    '-DLIBIGL_RESTRICTED_MOSEK=OFF',
    '-DLIBIGL_RESTRICTED_TRIANGLE=OFF'
)
if ($isWasm) { Invoke-CvcWasmCMakeBuild $iglArgs } else { Invoke-CvcCMakeBuild $iglArgs }

$inc = Join-Path $env:CVC_INSTALL_DIR 'include\igl'
$cm  = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake'

# ── complete the header-only tree (top level only, never module subdirs) ──
Get-ChildItem -LiteralPath (Join-Path $env:CVC_SOURCE_DIR 'include\igl') -File |
    Copy-Item -Destination $inc -Force
foreach ($f in 'raytri.c', 'IO', 'Singular_Value_Decomposition_Preamble.hpp', 'AABB.h', 'AABB.cpp') {
    if (-not (Test-Path -LiteralPath (Join-Path $inc $f) -PathType Leaf)) {
        throw "libigl: include/igl/$f missing after install"
    }
}
if (Get-ChildItem -LiteralPath $inc -Directory) {
    throw 'libigl: include/igl has subdirectories (a non-core module leaked)'
}
if ((Test-Path -LiteralPath (Join-Path $env:CVC_INSTALL_DIR 'include\Eigen')) -or
    (Test-Path -LiteralPath (Join-Path $cm 'eigen'))) {
    throw 'libigl: a FetchContent Eigen was installed next to libigl'
}

# ── make the package config findable as find_package(libigl [2.6]) ──
$src = Join-Path $cm 'igl'
$dst = Join-Path $cm 'libigl'
if (Test-Path -LiteralPath $src) {
    if (Test-Path -LiteralPath $dst) { Remove-Item -Recurse -Force -LiteralPath $dst }
    Move-Item -LiteralPath $src -Destination $dst
}
$cfg     = Join-Path $dst 'libigl-config.cmake'
$targets = Join-Path $dst 'LibiglConfigTargets.cmake'
if (-not (Test-Path -LiteralPath $cfg)) {
    throw 'libigl: lib\cmake\libigl\libigl-config.cmake missing after install'
}
if (-not (Select-String -Quiet -SimpleMatch -LiteralPath $targets -Pattern 'add_library(igl::core ')) {
    throw 'libigl: exported target is not igl::core'
}
$serial = [bool](Select-String -Quiet -SimpleMatch -LiteralPath $targets -Pattern 'IGL_PARALLEL_FOR_FORCE_SERIAL')
if ($serial -ne ($backend -eq 'SERIAL')) {
    throw "libigl: IGL_PARALLEL_FOR_FORCE_SERIAL export does not match backend $backend"
}

# CMake's own SameMajorVersion file under the name CMake pairs with
# libigl-config.cmake; header-only, so ARCH_INDEPENDENT.
Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath (Join-Path $dst 'LibiglConfigVersion.cmake')
$verScript = Join-Path $env:CVC_BUILD_DIR 'cvcpkg-libigl-version.cmake'
Set-Content -LiteralPath $verScript -Encoding Ascii -Value @(
    'cmake_minimum_required(VERSION 3.14)',
    'include(CMakePackageConfigHelpers)',
    'file(TO_CMAKE_PATH "${OUT}" OUT)',
    'write_basic_package_version_file("${OUT}" VERSION "${VER}" COMPATIBILITY SameMajorVersion ARCH_INDEPENDENT)'
)
& cmake "-DOUT=$(Join-Path $dst 'libigl-config-version.cmake')" "-DVER=$env:CVC_VERSION" -P $verScript
if ($LASTEXITCODE -ne 0) { throw 'libigl: writing libigl-config-version.cmake failed' }

# ── Eigen 5 is a config package: keep a FindEigen3.cmake on the consumer's
#    module path (CGAL appends one) from hijacking find_dependency(Eigen3) ──
$text = Get-Content -Raw -LiteralPath $cfg
$text = $text.Replace('find_dependency(Eigen3 REQUIRED)', 'find_dependency(Eigen3 CONFIG REQUIRED)')
Set-Content -LiteralPath $cfg -Value $text -NoNewline
if (-not (Select-String -Quiet -SimpleMatch -LiteralPath $cfg -Pattern 'find_dependency(Eigen3 CONFIG REQUIRED)')) {
    throw 'libigl: could not pin find_dependency(Eigen3) to CONFIG mode'
}

$licenses = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\libigl'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null
Copy-Item -Force -LiteralPath (Join-Path $env:CVC_SOURCE_DIR 'LICENSE.MPL2') -Destination $licenses

# ── smoke/: find_package(libigl 2.6) + igl::core, built and run here ──
$smokeBuild = Join-Path $env:CVC_BUILD_DIR 'cvcpkg-smoke'
$roots = (@($env:CVC_INSTALL_DIR, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }) -join ';'
$smokeArgs = @(
    '-G', 'Ninja',
    '-S', (Join-Path $recipeDir 'smoke'),
    '-B', $smokeBuild,
    '-DCMAKE_BUILD_TYPE=Release',
    "-DCMAKE_PREFIX_PATH=$roots",
    '-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF'
)
if ($isWasm) {
    # The Emscripten toolchain searches packages under the find roots only.
    $smokeArgs += @("-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain", "-DCMAKE_FIND_ROOT_PATH=$roots")
} else {
    $smokeArgs += @('-DCMAKE_CXX_COMPILER=cl', "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime")
}
# As in Invoke-CvcCMakeBuild: keep MinGW/MSYS2 headers away from cl.exe.
$origPath = $env:PATH
$env:PATH = ($env:PATH -split ';' |
    Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'
try {
    & cmake @smokeArgs
    if ($LASTEXITCODE -ne 0) { throw 'libigl smoke: cmake configure failed' }
    & cmake --build $smokeBuild
    if ($LASTEXITCODE -ne 0) { throw 'libigl smoke: build failed' }
} finally {
    $env:PATH = $origPath
}

if ($isWasm) {
    $node = $env:EMSDK_NODE
    if (-not $node -or -not (Test-Path -LiteralPath $node)) {
        $node = (Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    }
    if (-not $node) {
        Write-Warning 'libigl smoke: built for wasm, but no node to run it (emsdk has none)'
        return
    }
    & $node (Join-Path $smokeBuild 'igl_smoke.js')
} else {
    & (Join-Path $smokeBuild 'igl_smoke.exe')
}
if ($LASTEXITCODE -ne 0) { throw "libigl smoke: igl_smoke failed (exit $LASTEXITCODE)" }
Write-Host '-- libigl smoke passed --'
