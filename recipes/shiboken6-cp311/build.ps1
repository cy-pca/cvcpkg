# recipes/shiboken6-cp311/build.ps1 — build the sources/shiboken6 CMake project
# from the pinned Qt-for-Python pyside-setup 6.8.2 tarball on Windows (MSVC),
# against the cvcpkg python311 interpreter + cvcpkg qt6 6.8.2 + hermetic llvm18.
#
# Windows port of build.sh. Same NO-BUNDLE / SINGLE-QT contract: configure the
# sources/shiboken6 CMake project directly (route b), NOT setup.py --standalone,
# so Qt and libclang are linked by name and never copied into the install tree —
# one Qt6Core in-process with the host app.
#
# Produces: bin/shiboken6.exe (generator, links Qt6Core), lib/shiboken6*.lib +
# the shiboken6 DLL, the importable cp311 `shiboken6` package (wrapInstance) in
# Lib/site-packages, and the Shiboken6 CMake config the `pyside6` recipe consumes.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# ── Locate the cvcpkg python3.11 interpreter (cp311) in the dependency closure ─
# python311's Windows layout puts python.exe at the prefix root (python.org /
# PC\layout tree), so build the bindings + module against the SAME cp311 the host
# embeds. (Mirror of vtk-python-cp311/build.ps1.)
$pyExe = $null
$pyRoot = $null
foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX, $env:CVC_INSTALL_DIR)) {
    if (-not $root) { continue }
    foreach ($cand in @("$root\python.exe", "$root\bin\python.exe", "$root\python3.11.exe")) {
        if (Test-Path $cand) { $pyExe = $cand; $pyRoot = (Split-Path -Parent $cand); break }
    }
    if ($pyExe) { break }
}
if (-not $pyExe) { throw "shiboken6: could not find python (3.11) in the dependency closure" }
Write-Host "shiboken6: building against $pyExe"

# ── libclang 18 from the cvcpkg llvm18 dependency, and from nowhere else ─────
# shiboken's setup_clang() reads LLVM_INSTALL_DIR to find ClangConfig.cmake and
# libclang. The generator reads it again at run time to find Clang's builtin
# (resource) headers under $LLVM_INSTALL_DIR\lib\clang\<ver>\include, and it
# runs during this build: shibokenmodule's wrappers are generated with it.
# llvm18 is a declared dependency, so it is in the runtime closure
# (CVC_DEPS_PREFIX) or the build closure (CVC_BUILD_PREFIX). Look there only:
# an LLVM_INSTALL_DIR / CLANG_INSTALL_DIR inherited from the runner is
# discarded, so a preinstalled C:\Program Files\LLVM is never picked up.
$env:LLVM_INSTALL_DIR = $null
$env:CLANG_INSTALL_DIR = $null
foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX)) {
    if ($root -and (Test-Path "$root\lib\cmake\clang\ClangConfig.cmake")) {
        $env:LLVM_INSTALL_DIR = $root; break
    }
}
if (-not $env:LLVM_INSTALL_DIR) {
    throw ("shiboken6: llvm18 not found: no lib\cmake\clang\ClangConfig.cmake under " +
        "CVC_DEPS_PREFIX='$($env:CVC_DEPS_PREFIX)' or CVC_BUILD_PREFIX='$($env:CVC_BUILD_PREFIX)'. " +
        "Install this recipe's dependency closure (cvcpkg install-deps).")
}
# The generator loads libclang.dll (linked through libclang.lib), and libclang
# cannot parse the Qt or MSVC STL headers without the resource headers.
foreach ($need in @('bin\libclang.dll', 'lib\libclang.lib', 'lib\clang\18\include\stddef.h')) {
    if (-not (Test-Path "$($env:LLVM_INSTALL_DIR)\$need")) {
        throw "shiboken6: $($env:LLVM_INSTALL_DIR)\$need is missing; the llvm18 bundle is incomplete"
    }
}
Write-Host "shiboken6: LLVM_INSTALL_DIR=$($env:LLVM_INSTALL_DIR) (cvcpkg llvm18)"

# The shiboken6 generator is invoked during THIS build (it self-generates); it
# links Qt6Core + libclang + libshiboken. On Windows those resolve off PATH
# (DLL search) rather than an rpath, so prepend the dep bin dirs. (Direct-exe
# spawns use the 32KB env block, not cmd's ~8KB command-line limit, so a modest
# prepend here is safe — unlike vcvars/MSBuild custom-builds, see python311.)
$depsPrefix = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$env:PATH = "$($env:LLVM_INSTALL_DIR)\bin;$depsPrefix\bin;$env:CVC_INSTALL_DIR\bin;$env:PATH"

# Pass FORWARD-SLASH paths to cmake. shiboken bakes -DPYTHON_SITE_PACKAGES into
# Shiboken6Config's SHIBOKEN_PYTHON_MODULE_DIR verbatim; a backslash there
# (\Lib -> \L, \Users -> \U) is an invalid CMake string escape that breaks a
# downstream find_package(Shiboken6) (pyside6). cmake accepts forward slashes on
# Windows, and Invoke-CvcRewriteInstallPaths only fixes the install-dir PREFIX,
# not backslashes in the remainder — so normalize every path we hand cmake.
$prefixPathRaw = if ($env:CMAKE_PREFIX_PATH) { $env:CMAKE_PREFIX_PATH } else { $depsPrefix }
$installFwd = $env:CVC_INSTALL_DIR -replace '\\', '/'
$srcFwd = "$env:CVC_SOURCE_DIR\sources\shiboken6" -replace '\\', '/'
$buildFwd = $env:CVC_BUILD_DIR -replace '\\', '/'
$sitePackages = "$installFwd/Lib/site-packages"
$prefixPathFwd = $prefixPathRaw -replace '\\', '/'
$pyExeFwd = $pyExe -replace '\\', '/'
$pyRootFwd = $pyRoot -replace '\\', '/'
$llvmFwd = $env:LLVM_INSTALL_DIR -replace '\\', '/'

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
    "-DClang_DIR=$llvmFwd/lib/cmake/clang",
    "-DLLVM_DIR=$llvmFwd/lib/cmake/llvm",
    '-DSHIBOKEN_BUILD_TOOLS=ON',
    '-DSHIBOKEN_BUILD_LIBS=ON',
    '-DFORCE_LIMITED_API=yes',
    '-DBUILD_TESTS=OFF'
)

# Strip MinGW/MSYS2 from PATH before the MSVC cmake configure. CMake's
# find_path()/find_library() walk <PATH-entry>\..\include and \..\lib, so
# C:\msys64\mingw64\bin on the runner's PATH drags MinGW-w64 gcc headers into
# cl.exe (C2061/C2146 storms). shiboken is find_package-heavy (Clang, Qt6,
# Python), so guard it the same way env-windows.ps1 / python-wheel.ps1 do.
$env:PATH = ($env:PATH -split ';' | Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "shiboken6: cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "shiboken6: cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "shiboken6: cmake install failed" }

# Make the installed .cmake/.pc files relocatable. cmake bakes the absolute
# build-time CVC_INSTALL_DIR (with backslashes) into Shiboken6Config.abi3.cmake's
# SHIBOKEN_PYTHON_MODULE_DIR; a downstream find_package(Shiboken6) (pyside6) then
# fails to even parse it ("Invalid character escape '\U'" from C:\Users\...) and
# can't locate the module. Rewrite absolute paths to a ${CMAKE_CURRENT_LIST_DIR}
# anchor — the raw-cmake analog of what Invoke-CvcCMakeBuild does automatically,
# and the Windows counterpart of build.sh's cvc_rewrite_install_paths.
Invoke-CvcRewriteInstallPaths
