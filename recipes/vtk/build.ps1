# recipes/vtk/build.ps1 — build VTK from source on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# VTK is always built shared.
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
    '-DVTK_WRAP_PYTHON=OFF',
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
