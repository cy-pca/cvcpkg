# recipes/mujoco3-cp312/build.ps1 — build the MuJoCo 3.17.0 Python bindings
# (PyPI mujoco 3.17.0, `import mujoco`) FROM SOURCE on Windows.  Windows twin of
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

if (-not $env:CVC_PYTHON_ABI) { $env:CVC_PYTHON_ABI = 'cp312' }
if (-not $env:CVC_PYTHON_INTERPRETER) { $env:CVC_PYTHON_INTERPRETER = 'python312' }
$name = "mujoco3-$env:CVC_PYTHON_ABI"
$py = Get-CvcPythonExe
Write-Output "${name}: building with $py"

# python-wheel.ps1 has already imported the MSVC environment (vcvars64) and put
# the prefixes' bin\ (cmake, ninja) on PATH.
$bp = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $env:CVC_DEPS_PREFIX }

# ── 1. Bridge the build-only backend (setuptools) onto sys.path ─────────────
$env:PYTHONPATH = Join-Path $bp 'Lib\site-packages'

# ── 2. Stage the C library from the `mujoco3` recipe ─────────────────────────
$sdk = Join-Path $env:CVC_BUILD_DIR 'mujoco-sdk'
if (Test-Path -LiteralPath $sdk) { Remove-Item -Recurse -Force -LiteralPath $sdk }
foreach ($d in 'lib', 'bin', 'include', 'plugin') {
    New-Item -ItemType Directory -Force -Path (Join-Path $sdk $d) | Out-Null
}
$hdr = Join-Path $bp 'include\mujoco\mujoco.h'
if (-not (Test-Path -LiteralPath $hdr)) { throw "${name}: $hdr not found (is the mujoco3 recipe staged?)" }
Copy-Item -Recurse -Force -LiteralPath (Join-Path $bp 'include\mujoco') -Destination (Join-Path $sdk 'include')
Copy-Item -Force -LiteralPath (Join-Path $bp 'lib\mujoco.lib') -Destination (Join-Path $sdk 'lib')
Copy-Item -Force -LiteralPath (Join-Path $bp 'bin\mujoco.dll') -Destination (Join-Path $sdk 'bin')
Copy-Item -Force -Path (Join-Path $bp 'bin\mujoco_plugin\*.dll') -Destination (Join-Path $sdk 'plugin')
$env:MUJOCO_PATH = $sdk
$env:MUJOCO_PLUGIN_PATH = Join-Path $sdk 'plugin'

# ── 3. Pinned sources for the bindings' CMake ───────────────────────────────
$deps = Invoke-MjFetchDeps
$env:MUJOCO_CMAKE = (Get-Command cmake.exe).Source
# /permissive for structs_wrappers.cc ONLY.  Under /permissive- (implied by the
# /std:c++20 the bindings use with MSVC, and present in no other form: no
# single /Zc:...- switch undoes it), MSVC 19.44 gives the MjWrapper
# specialisations over mjVisual's anonymous nested structs (headlight, rgba)
# internal linkage, so structs_wrappers.cc emits their constructors as Static
# and _structs fails with LNK2019 on the copy constructors structs.cc calls.
# /permissive restores external linkage, but applied to every target it also
# turns off two-phase lookup, and _functions then recurses until the stack
# overflows (0xC00000FD) at import.  So it goes on the one target that emits
# those definitions, as a target option (after -std:c++20, where it has to be),
# added by a deferred call from a project include.  Checked on 2026-11-09:
# check.py passes and copy.copy/deepcopy of MjModel.vis.headlight/rgba work.
$permissive = Join-Path $env:CVC_BUILD_DIR 'mujoco-msvc-permissive.cmake'
[System.IO.File]::WriteAllText($permissive, @'
if(MSVC)
  get_property(_cvc_mj_done GLOBAL PROPERTY CVC_MUJOCO_PERMISSIVE_DEFERRED)
  if(NOT _cvc_mj_done)
    set_property(GLOBAL PROPERTY CVC_MUJOCO_PERMISSIVE_DEFERRED TRUE)
    function(_cvc_mujoco_permissive_structs_wrappers)
      if(NOT TARGET structs_wrappers)
        message(FATAL_ERROR "cvcpkg: target structs_wrappers not found; revisit the /permissive workaround")
      endif()
      target_compile_options(structs_wrappers PRIVATE /permissive)
    endfunction()
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL _cvc_mujoco_permissive_structs_wrappers)
  endif()
endif()
'@, (New-Object System.Text.UTF8Encoding($false)))
# IPO stays at setup.py's default (on) here, unlike build.sh.  Under MSVC, IPO
# off made cl spend 100+ CPU-minutes optimising functions.cc on one core
# (2026-11-09, stopped unfinished), while /GL + the multi-threaded /LTCG link
# built it in about ten minutes.
#
# FETCHCONTENT_BASE_DIR: keep object paths under Windows' 260-character limit.
# setup.py builds in <src>\build\temp.win-amd64-cpython-3XX\Release\, and
# FetchContent puts abseil's build tree under that, so on the dev fleet
# (C:\Users\tfx\cvcpkg-builder-dev\cvcpkg-job-...\cvcpkg-...\) the deepest
# abseil object reached 282 characters and cl failed with "fatal error C1083:
# Cannot open compiler generated file: '': Invalid argument".  Under
# <build dir>\fc the same object is 240.  Upstream's wheel CI also points
# FETCHCONTENT_BASE_DIR at a short directory.
$fcBase = (Join-Path $env:CVC_BUILD_DIR 'fc') -replace '\\', '/'
$env:MUJOCO_CMAKE_ARGS = "-GNinja -DMUJOCO_CMAKE_DEP_CACHE=$($deps.Cache) -DFETCHCONTENT_BASE_DIR=$fcBase -DCMAKE_PROJECT_INCLUDE=$($permissive -replace '\\', '/')"

# ── 4. Build the wheel from the extracted sdist ─────────────────────────────
$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
$log = Join-Path $env:CVC_BUILD_DIR 'mujoco-build.log'
& $py -m pip wheel -v --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR *>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) {
    $rc = $LASTEXITCODE
    Write-MjBuildErrors -Log $log
    throw "${name}: pip wheel failed ($rc)"
}
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
