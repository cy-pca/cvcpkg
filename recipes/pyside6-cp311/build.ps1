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

# ── libclang 18 from the cvcpkg llvm18 dependency, and from nowhere else ─────
# The shiboken6 generator runs during THIS build to parse the Qt headers. It
# loads libclang.dll and reads LLVM_INSTALL_DIR at run time to find Clang's
# builtin (resource) headers under $LLVM_INSTALL_DIR\lib\clang\<ver>\include.
# llvm18 is a declared build dependency (and a runtime one of shiboken6), so
# it is in the runtime closure (CVC_DEPS_PREFIX) or the build closure
# (CVC_BUILD_PREFIX). Look there only: an LLVM_INSTALL_DIR / CLANG_INSTALL_DIR
# inherited from the runner is discarded, so a preinstalled
# C:\Program Files\LLVM is never picked up.
$env:LLVM_INSTALL_DIR = $null
$env:CLANG_INSTALL_DIR = $null
foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX)) {
    if ($root -and (Test-Path "$root\lib\cmake\clang\ClangConfig.cmake")) {
        $env:LLVM_INSTALL_DIR = $root; break
    }
}
if (-not $env:LLVM_INSTALL_DIR) {
    throw ("pyside6: llvm18 not found: no lib\cmake\clang\ClangConfig.cmake under " +
        "CVC_DEPS_PREFIX='$($env:CVC_DEPS_PREFIX)' or CVC_BUILD_PREFIX='$($env:CVC_BUILD_PREFIX)'. " +
        "Install this recipe's dependency closure (cvcpkg install-deps).")
}
# The generator loads libclang.dll (linked through libclang.lib), and libclang
# cannot parse the Qt or MSVC STL headers without the resource headers.
foreach ($need in @('bin\libclang.dll', 'lib\libclang.lib', 'lib\clang\18\include\stddef.h')) {
    if (-not (Test-Path "$($env:LLVM_INSTALL_DIR)\$need")) {
        throw "pyside6: $($env:LLVM_INSTALL_DIR)\$need is missing; the llvm18 bundle is incomplete"
    }
}
Write-Host "pyside6: LLVM_INSTALL_DIR=$($env:LLVM_INSTALL_DIR) (cvcpkg llvm18)"

# The generator (bin/shiboken6.exe, from the shiboken6 package) must resolve, at
# RUN time during this build: libclang (llvm18), Qt6Core (qt6), and shiboken6 —
# all off PATH (DLL search) on Windows. Put the dep bin dirs first.
$depsPrefix = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$env:PATH = "$($env:LLVM_INSTALL_DIR)\bin;$depsPrefix\bin;$env:CVC_BUILD_PREFIX\bin;$env:CVC_INSTALL_DIR\bin;$env:PATH"

# Pass FORWARD-SLASH paths to cmake — a backslash baked into a generated config
# (\Lib -> \L) is an invalid CMake string escape downstream (see shiboken6 build.ps1).
$prefixPathRaw = if ($env:CMAKE_PREFIX_PATH) { $env:CMAKE_PREFIX_PATH } else { $depsPrefix }
$installFwd = $env:CVC_INSTALL_DIR -replace '\\', '/'
$srcFwd = "$env:CVC_SOURCE_DIR\sources\pyside6" -replace '\\', '/'
$buildFwd = $env:CVC_BUILD_DIR -replace '\\', '/'
$sitePackages = "$installFwd/Lib/site-packages"
$prefixPathFwd = $prefixPathRaw -replace '\\', '/'
$pyExeFwd = $pyExe -replace '\\', '/'
$pyRootFwd = $pyRoot -replace '\\', '/'

# Module subset — ONLY modules the feature-lean cvcpkg qt6 provides
# (Core, Gui, Widgets, OpenGL, OpenGLWidgets; no Qml/Quick/Sql).
$pysideModules = "Core;Gui;Widgets;OpenGL;OpenGLWidgets"

$allArgs = @(
    '-G', 'Ninja',
    '-S', $srcFwd,
    '-B', $buildFwd,
    "-DCMAKE_INSTALL_PREFIX=$installFwd",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    "-DCMAKE_PREFIX_PATH=$prefixPathFwd",
    "-DPython_EXECUTABLE=$pyExeFwd",
    "-DPython_ROOT_DIR=$pyRootFwd",
    '-DPython_FIND_STRATEGY=LOCATION',
    "-DPYTHON_SITE_PACKAGES=$sitePackages",
    "-DMODULES=$pysideModules",
    '-DSTANDALONE=0',
    '-DFORCE_LIMITED_API=yes',
    '-DBUILD_TESTS=OFF',
    # Skip .pyi type-stub generation. It runs the freshly-built PySide6, whose
    # __init__._additional_dll_directories demands shiboken6 as a STANDALONE
    # sibling (ImportError: ...\shiboken6\libshiboken does not exist) — false for
    # our -DSTANDALONE=0 no-bundle build. Stubs are optional IDE hints; the
    # compiled bindings are unaffected. (PySideModules.cmake gates on DISABLE_PYI.)
    '-DDISABLE_PYI=ON'
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

# Make the installed .cmake/.pc files relocatable (see shiboken6-cp311 build.ps1)
# — cmake otherwise bakes the absolute build-time CVC_INSTALL_DIR into
# PySide6Config, breaking downstream find_package(PySide6).
Invoke-CvcRewriteInstallPaths
