# recipes/vtk/build-wasm.ps1 — cross-compile VTK to wasm.
# Qt, OpenGL2, and wrapping disabled for wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DBUILD_SHARED_LIBS=OFF',
    '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain",
    '-DVTK_GROUP_ENABLE_Qt=NO',
    '-DVTK_WRAP_PYTHON=OFF',
    '-DVTK_BUILD_TESTING=OFF',
    '-DVTK_BUILD_EXAMPLES=OFF',
    '-DVTK_BUILD_DOCUMENTATION=OFF',
    '-DVTK_LEGACY_REMOVE=ON',
    '-DVTK_MODULE_ENABLE_VTK_RenderingOpenGL2=NO',
    '-DVTK_MODULE_ENABLE_VTK_RenderingUI=NO',
    '-DVTK_MODULE_ENABLE_VTK_InteractionWidgets=DEFAULT',
    '-DVTK_ENABLE_WRAPPING=OFF'
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
