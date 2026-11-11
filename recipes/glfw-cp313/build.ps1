# recipes/glfw-cp313/build.ps1 — build pyGLFW 2.11.0 (PyPI `glfw`) FROM SOURCE.
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "glfw-cp313: building with $py"

# The build-only backend (setuptools, wheel) lives in the build prefix.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "glfw-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "glfw-cp313: no wheel produced under $wheelhouse" }
Write-Output "glfw-cp313: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "glfw-cp313: pip install failed ($LASTEXITCODE)" }

# The staged package sits in CVC_INSTALL_DIR, which holds no glfw3.dll, so the
# patched loader's second candidate, sys.base_prefix (the deps prefix holding
# python.exe and the glfw recipe's bin\glfw3.dll), is the one that must load.
# No double quotes in the snippet: it goes to python.exe as one argument.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck @'
import os, sys, glfw
rel = {'win32': ('bin', 'glfw3.dll'), 'darwin': ('lib', 'libglfw.3.dylib')}
want = os.path.join(sys.base_prefix, *rel.get(sys.platform, ('lib', 'libglfw.so.3')))
assert os.path.samefile(glfw._glfw._name, want), (glfw._glfw._name, want)
assert glfw.get_version()[:2] == (3, 4), glfw.get_version()
print('pyGLFW', glfw.__version__, 'loaded', glfw._glfw._name, glfw.get_version_string().decode())
'@
