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

# ── Hermetic libclang 18 (cvcpkg llvm18) for ApiExtractor ────────────────────
# shiboken's setup_clang() reads LLVM_INSTALL_DIR to find ClangConfig.cmake +
# libclang and, at generation time, the Clang builtin/resource headers under
# ${LLVM_INSTALL_DIR}/lib/clang/<ver>/include. Prefer the cvcpkg llvm18 in the
# dependency prefix (a hermetic dep of this recipe).
if (-not $env:LLVM_INSTALL_DIR) {
    foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX, $env:CVC_INSTALL_DIR)) {
        if ($root -and (Test-Path "$root\lib\cmake\clang\ClangConfig.cmake")) {
            $env:LLVM_INSTALL_DIR = $root; break
        }
    }
}
if (-not $env:LLVM_INSTALL_DIR) { throw "shiboken6: llvm18 (ClangConfig.cmake) not found in the closure" }
Write-Host "shiboken6: LLVM_INSTALL_DIR=$($env:LLVM_INSTALL_DIR)"
if (-not (Test-Path "$($env:LLVM_INSTALL_DIR)\lib\cmake\clang\ClangConfig.cmake")) {
    throw "shiboken6: ClangConfig.cmake missing under $($env:LLVM_INSTALL_DIR)"
}

# The shiboken6 generator is invoked during THIS build (it self-generates); it
# links Qt6Core + libclang + libshiboken. On Windows those resolve off PATH
# (DLL search) rather than an rpath, so prepend the dep bin dirs. (Direct-exe
# spawns use the 32KB env block, not cmd's ~8KB command-line limit, so a modest
# prepend here is safe — unlike vcvars/MSBuild custom-builds, see python311.)
$depsPrefix = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
$env:PATH = "$($env:LLVM_INSTALL_DIR)\bin;$depsPrefix\bin;$env:CVC_INSTALL_DIR\bin;$env:PATH"

# Windows site-packages is <prefix>\Lib\site-packages (not lib/python3.11/...).
$sitePackages = "$env:CVC_INSTALL_DIR\Lib\site-packages"

$prefixPath = if ($env:CMAKE_PREFIX_PATH) { $env:CMAKE_PREFIX_PATH } else { $depsPrefix }

$allArgs = @(
    '-G', 'Ninja',
    '-S', "$env:CVC_SOURCE_DIR\sources\shiboken6",
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    "-DCMAKE_PREFIX_PATH=$prefixPath",
    "-DPython_EXECUTABLE=$pyExe",
    "-DPython_ROOT_DIR=$pyRoot",
    '-DPython_FIND_STRATEGY=LOCATION',
    "-DPYTHON_SITE_PACKAGES=$sitePackages",
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
