# recipes/markupsafe-cp313t/build.ps1 — Windows from-source build of MarkupSafe 3.0.3 for
# the cp313t interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time.
#
# DELTA vs build.sh: markupsafe._speedups links only the CRT, so there is no rpath
# or DLL-search problem to solve. CIBUILDWHEEL=1 carries over unchanged — the
# silent "retry without the C extension" path in setup.py is platform-independent
# and is the one thing that could make this column ship a slow, valid-looking
# wheel.
$env:CIBUILDWHEEL = '1'

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "markupsafe-cp313t: building with $py"

# Bridge BUILD-only python columns (setuptools, ...) into the DEPS-prefix
# interpreter — same reason as build.sh: they live in CVC_BUILD_PREFIX and
# --no-build-isolation imports them straight off sys.path.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools; print("setuptools", setuptools.__version__, setuptools.__file__)'
if ($LASTEXITCODE -ne 0) { throw "markupsafe-cp313t: setuptools not importable from the build prefix" }

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "markupsafe-cp313t: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "markupsafe-cp313t: no wheel produced under $wheelhouse" }
Write-Output "markupsafe-cp313t: built $($wheel.Name)"

# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree, so installing --prefix into
# the initially-empty per-recipe dir is what keeps the staged tree pure.
& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "markupsafe-cp313t: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "markupsafe-cp313t: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "markupsafe-cp313t: staged into $sitePackages"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import markupsafe
# ImportError here means setup.py took its silent pure-Python path and we were
# about to ship a markupsafe with no C accelerator in it.
from markupsafe import _speedups

assert _speedups.__file__.endswith((".so", ".pyd", ".dylib")), _speedups.__file__
# Not just "the module exists" — the escape path actually bound to the C one.
assert markupsafe._escape_inner is _speedups._escape_inner, "escape path is not the C one"

assert markupsafe.escape("<a href='x'>&") == "&lt;a href=&#39;x&#39;&gt;&amp;"
assert markupsafe.Markup("<b>{}</b>").format("<i>") == "<b>&lt;i&gt;</b>"
assert markupsafe.escape(markupsafe.Markup("<b>ok</b>")) == "<b>ok</b>"

print("markupsafe   :", markupsafe.__file__)
print("_speedups    :", _speedups.__file__)
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "markupsafe-cp313t: verification failed ($LASTEXITCODE)" }

Write-Output "markupsafe-cp313t: build + verification complete"
