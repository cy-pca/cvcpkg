# recipes/vtk-python/build.ps1 — rebuild VTK 9.5 from the SAME pinned source as
# the `vtk` recipe but with -DVTK_WRAP_PYTHON=ON, packaging ONLY the Python
# wrapper artifacts. See build.sh for the full rationale and the ABI contract:
# the C++ cmake flags here are kept identical to recipes/vtk/build.ps1 so the
# wrapped ABI matches the `vtk` package's C++ libraries.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# Locate the cvcpkg python311 interpreter (cp311) in the dependency closure so the
# wrappers match the ABI pycvc / pycvc-gl use.
$pyExe = $null
foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX, $env:CVC_INSTALL_DIR)) {
    if (-not $root) { continue }
    foreach ($cand in @("$root\python.exe", "$root\bin\python.exe", "$root\python3.11.exe")) {
        if (Test-Path $cand) { $pyExe = $cand; break }
    }
    if ($pyExe) { break }
}
if (-not $pyExe) { throw "vtk-python: could not find python (3.11) in the dependency closure" }
Write-Host "vtk-python: wrapping against $pyExe"

$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DBUILD_SHARED_LIBS=ON',
    '-DVTK_GROUP_ENABLE_Qt=YES',
    '-DVTK_QT_VERSION=6',
    '-DVTK_MODULE_ENABLE_VTK_GUISupportQtQuick=NO',
    '-DVTK_MODULE_ENABLE_VTK_RenderingQtQuick=NO',
    '-DVTK_WRAP_PYTHON=ON',
    '-DVTK_PYTHON_VERSION=3',
    "-DPython3_EXECUTABLE=$pyExe",
    '-DPython3_FIND_STRATEGY=LOCATION',
    '-DVTK_PYTHON_SITE_PACKAGES_SUFFIX=Lib/site-packages',
    '-DVTK_BUILD_TESTING=OFF',
    '-DVTK_BUILD_EXAMPLES=OFF',
    '-DVTK_BUILD_DOCUMENTATION=OFF',
    '-DVTK_LEGACY_REMOVE=ON'
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
