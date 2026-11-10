# recipes/pyopengl-cp313/build.ps1 — build pyopengl 3.1.10 FROM SOURCE (generated).
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "pyopengl-cp313: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

# The sdist ships prebuilt freeglut and GLE DLLs for Windows: delete them
# before they land in the wheel (_SDIST_PRUNE in tools/gen_python_recipes.py).
Remove-Item -Path (Join-Path $env:CVC_SOURCE_DIR 'OpenGL/DLLS/*') -Recurse -Force

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "pyopengl-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "pyopengl-cp313: no wheel produced under $wheelhouse" }
Write-Output "pyopengl-cp313: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pyopengl-cp313: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck 'import os, OpenGL, OpenGL.arrays; assert OpenGL.__version__ == ''3.1.10'', OpenGL.__version__; d = os.path.join(os.path.dirname(OpenGL.__file__), ''DLLS''); assert not os.path.exists(d) or not os.listdir(d), os.listdir(d)'
