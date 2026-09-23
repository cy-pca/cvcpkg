# recipes/vtk/build-wasm.ps1 — cross-compile VTK to wasm.
# Qt and wrapping disabled for wasm. The RENDERING modules build against
# Emscripten's WebGL2/GLES3 backend (matches build-wasm.sh, the linux host path):
# without them the bundle is compute-only and won't link the cvcGL browser demos.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

# env-wasm.ps1 adds -pthread to the flags when CVC_WASM_THREADS=1; VTK additionally
# needs its own switch to size worker pools and enable the threaded SMP backend.
$vtkThreads = if ($env:CVC_WASM_THREADS -eq '1') { 'ON' } else { 'OFF' }

$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DBUILD_SHARED_LIBS=OFF',
    '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain",
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX",
    "-DCMAKE_FIND_ROOT_PATH=$env:CVC_DEPS_PREFIX",
    '-DVTK_GROUP_ENABLE_Qt=NO',
    '-DVTK_WRAP_PYTHON=OFF',
    '-DVTK_BUILD_TESTING=OFF',
    '-DVTK_BUILD_EXAMPLES=OFF',
    '-DVTK_BUILD_DOCUMENTATION=OFF',
    '-DVTK_LEGACY_REMOVE=ON',
    '-DVTK_MODULE_ENABLE_VTK_RenderingOpenGL2=YES',
    '-DVTK_MODULE_ENABLE_VTK_RenderingUI=YES',
    '-DVTK_MODULE_ENABLE_VTK_RenderingVolume=YES',
    '-DVTK_MODULE_ENABLE_VTK_RenderingVolumeOpenGL2=YES',
    '-DVTK_MODULE_ENABLE_VTK_RenderingAnnotation=YES',
    '-DVTK_MODULE_ENABLE_VTK_RenderingFreeType=YES',
    '-DVTK_MODULE_ENABLE_VTK_InteractionStyle=YES',
    '-DVTK_MODULE_ENABLE_VTK_IOImage=YES',
    '-DVTK_MODULE_ENABLE_VTK_InteractionWidgets=DEFAULT',
    "-DVTK_WEBASSEMBLY_THREADS=$vtkThreads",
    '-DVTK_ENABLE_WRAPPING=OFF'
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

# Cap parallelism: this box has 16 GB RAM and emcc compiles are ~1-2 GB each, so
# the default nproc (20) OOMs the compiler mid-build. 6 is the safe ceiling here.
$vtkJobs = if ($env:CVC_JOBS -and [int]$env:CVC_JOBS -lt 6) { [int]$env:CVC_JOBS } else { 6 }
& cmake --build $env:CVC_BUILD_DIR -j $vtkJobs
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
