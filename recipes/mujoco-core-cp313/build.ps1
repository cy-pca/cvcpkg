# recipes/mujoco-core-cp313/build.ps1 — build the MuJoCo Python bindings
# (PyPI mujoco-core, `import mujoco`) FROM SOURCE on Windows.  Windows twin of
# build.sh; same contract (see that file for the why).
#
# setup.py drives CMake itself; MUJOCO_CMAKE_ARGS selects Ninja so the MSVC
# environment python-wheel.ps1 imports is used (not a Visual Studio generator
# search).  The wheel carries mujoco.dll and the plugin DLLs next to
# the .pyd files, where Python's extension-module DLL lookup finds them.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
. "$PSScriptRoot\deps.ps1"

if (-not $env:CVC_PYTHON_ABI) { $env:CVC_PYTHON_ABI = 'cp313' }
if (-not $env:CVC_PYTHON_INTERPRETER) { $env:CVC_PYTHON_INTERPRETER = 'python313' }
$name = "mujoco-core-$env:CVC_PYTHON_ABI"
$py = Get-CvcPythonExe
Write-Output "${name}: building with $py"

# python-wheel.ps1 has already imported the MSVC environment (vcvars64) and put
# the prefixes' bin\ (cmake, ninja) on PATH.
$bp = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $env:CVC_DEPS_PREFIX }

# ── 1. Bridge the build-only backend (setuptools) onto sys.path ─────────────
$env:PYTHONPATH = Join-Path $bp 'Lib\site-packages'

# ── 2. Stage the C library from the `mujoco` recipe ─────────────────────────
$sdk = Join-Path $env:CVC_BUILD_DIR 'mujoco-sdk'
if (Test-Path -LiteralPath $sdk) { Remove-Item -Recurse -Force -LiteralPath $sdk }
foreach ($d in 'lib', 'bin', 'include', 'plugin') {
    New-Item -ItemType Directory -Force -Path (Join-Path $sdk $d) | Out-Null
}
$hdr = Join-Path $bp 'include\mujoco\mujoco.h'
if (-not (Test-Path -LiteralPath $hdr)) { throw "${name}: $hdr not found (is the mujoco recipe staged?)" }
Copy-Item -Recurse -Force -LiteralPath (Join-Path $bp 'include\mujoco') -Destination (Join-Path $sdk 'include')
Copy-Item -Force -LiteralPath (Join-Path $bp 'lib\mujoco.lib') -Destination (Join-Path $sdk 'lib')
Copy-Item -Force -LiteralPath (Join-Path $bp 'bin\mujoco.dll') -Destination (Join-Path $sdk 'bin')
Copy-Item -Force -Path (Join-Path $bp 'bin\mujoco_plugin\*.dll') -Destination (Join-Path $sdk 'plugin')
$env:MUJOCO_PATH = $sdk
$env:MUJOCO_PLUGIN_PATH = Join-Path $sdk 'plugin'

# ── 3. Pinned sources for the bindings' CMake ───────────────────────────────
$deps = Invoke-MjFetchDeps
$env:MUJOCO_CMAKE = (Get-Command cmake.exe).Source
$env:MUJOCO_CMAKE_ARGS = "-GNinja -DMUJOCO_CMAKE_DEP_CACHE=$($deps.Cache)"
# /permissive: the bindings compile as C++20 under MSVC (mujoco/CMakeLists.txt,
# "for forceinline lambdas"), and /std:c++20 implies /permissive-, under which
# MSVC 19.44 gives the MjWrapper specialisations over mjVisual's anonymous
# nested structs (headlight, rgba) internal linkage: structs_wrappers.cc's
# definitions become Static and _structs fails with LNK2019 on their copy
# constructors.  /permissive restores external linkage (checked with a
# two-line repro and dumpbin).  CXXFLAGS rather than -DCMAKE_CXX_FLAGS, which
# would replace CMake's /EHsc /GR defaults instead of adding to them.
$env:CXXFLAGS = "/permissive $env:CXXFLAGS".Trim()

# ── 4. Build the wheel from the extracted sdist ─────────────────────────────
$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
$log = Join-Path $env:CVC_BUILD_DIR 'mujoco-core-build.log'
& $py -m pip wheel -v --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR *>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) { throw "${name}: pip wheel failed ($LASTEXITCODE)" }
Assert-MjNoGitFetch -Log $log

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "${name}: no wheel produced under $wheelhouse" }
Write-Output "${name}: built $($wheel.Name)"

# ── 5. Install it into this recipe's (empty) staging prefix ─────────────────
& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "${name}: pip install failed ($LASTEXITCODE)" }

Install-MjLicenses -Destination (Join-Path $env:CVC_INSTALL_DIR "share\licenses\$name") -Cache $deps.Cache

# ── 6. Verify against the RUNTIME closure only ──────────────────────────────
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'check.py'))
