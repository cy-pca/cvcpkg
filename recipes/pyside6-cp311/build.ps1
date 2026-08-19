# recipes/pyside6-cp311/build.ps1 — build the sources/pyside6 CMake project from
# the pinned Qt-for-Python pyside-setup 6.8.2 tarball on Windows (MSVC), against
# the cvcpkg python311 interpreter, the cvcpkg qt6 6.8.2, and the already-built
# cvcpkg shiboken6. The shiboken6 generator (which parses Qt headers with the
# hermetic llvm18 libclang) emits the C++ bindings; cmake compiles + installs the
# PySide6 module.
#
# Windows port of build.sh. NO-BUNDLE CONTRACT: -DSTANDALONE=0 links the cvcpkg
# qt6 by name and copies NO Qt DLLs into the install tree, so the embedded
# interpreter shares the host app's single Qt6Core — the precondition for
# shiboken6.wrapInstance(addr, QtWidgets.QMainWindow) on the live window.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# ── Locate the cvcpkg python3.11 interpreter (cp311) in the dependency closure ─
$pyExe = $null
$pyRoot = $null
foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX, $env:CVC_INSTALL_DIR)) {
    if (-not $root) { continue }
    foreach ($cand in @("$root\python.exe", "$root\bin\python.exe", "$root\python3.11.exe")) {
        if (Test-Path $cand) { $pyExe = $cand; $pyRoot = (Split-Path -Parent $cand); break }
    }
    if ($pyExe) { break }
}
if (-not $pyExe) { throw "pyside6: could not find python (3.11) in the dependency closure" }
Write-Host "pyside6: building against $pyExe"

# ── Hermetic libclang 18 (cvcpkg llvm18) — the shiboken6 generator runs during
# THIS build to parse Qt headers and links libclang. Pulled in transitively via
# shiboken6; find its prefix by ClangConfig.cmake.
if (-not $env:LLVM_INSTALL_DIR) {
    foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX, $env:CVC_INSTALL_DIR)) {
        if ($root -and (Test-Path "$root\lib\cmake\clang\ClangConfig.cmake")) {
            $env:LLVM_INSTALL_DIR = $root; break
        }
    }
}
if (-not $env:LLVM_INSTALL_DIR) { throw "pyside6: llvm18 (ClangConfig.cmake) not found in the closure" }
Write-Host "pyside6: LLVM_INSTALL_DIR=$($env:LLVM_INSTALL_DIR)"

# The generator (bin/shiboken6.exe, from the shiboken6 package) must resolve, at
# RUN time during this build: libclang (llvm18), Qt6Core (qt6), and shiboken6 —
# all off PATH (DLL search) on Windows. Put the dep bin dirs first.
$depsPrefix = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$env:PATH = "$($env:LLVM_INSTALL_DIR)\bin;$depsPrefix\bin;$env:CVC_BUILD_PREFIX\bin;$env:CVC_INSTALL_DIR\bin;$env:PATH"

# Windows site-packages is <prefix>\Lib\site-packages.
$sitePackages = "$env:CVC_INSTALL_DIR\Lib\site-packages"
$prefixPath = if ($env:CMAKE_PREFIX_PATH) { $env:CMAKE_PREFIX_PATH } else { $depsPrefix }

# Module subset — ONLY modules the feature-lean cvcpkg qt6 provides
# (Core, Gui, Widgets, OpenGL, OpenGLWidgets; no Qml/Quick/Sql).
$pysideModules = "Core;Gui;Widgets;OpenGL;OpenGLWidgets"

$allArgs = @(
    '-G', 'Ninja',
    '-S', "$env:CVC_SOURCE_DIR\sources\pyside6",
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    "-DCMAKE_PREFIX_PATH=$prefixPath",
    "-DPython_EXECUTABLE=$pyExe",
    "-DPython_ROOT_DIR=$pyRoot",
    '-DPython_FIND_STRATEGY=LOCATION',
    "-DPYTHON_SITE_PACKAGES=$sitePackages",
    "-DMODULES=$pysideModules",
    '-DSTANDALONE=0',
    '-DFORCE_LIMITED_API=yes',
    '-DBUILD_TESTS=OFF'
)

# Strip MinGW/MSYS2 from PATH before the MSVC cmake configure (see shiboken6
# build.ps1) — CMake's find_path/find_library would otherwise pull MinGW-w64 gcc
# headers into cl.exe. pyside is even more find_package-heavy than shiboken.
$env:PATH = ($env:PATH -split ';' | Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "pyside6: cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "pyside6: cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "pyside6: cmake install failed" }
