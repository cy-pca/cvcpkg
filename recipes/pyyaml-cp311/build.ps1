# recipes/pyyaml-cp311/build.ps1 — Windows from-source build of PyYAML 6.0.3 for
# the cp311 interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time.
#
# DELTA vs build.sh — two Windows-specific things.
#
# 1. yaml.lib is found through the same pyyaml_build_config config-setting the
#    Unix path uses; MSVC turns those into /I and /LIBPATH:. libyaml's headers
#    default to __declspec(dllimport) unless YAML_DECLARE_STATIC is defined, which
#    is correct for the shared build recipes/yaml produces.
#
# 2. Python 3.8+ removed PATH from the DLL search path used for extension modules,
#    so a yaml.dll sitting in <prefix>\bin is NOT findable by _yaml.pyd sitting in
#    site-packages — `import yaml` would raise ImportError: DLL load failed on a
#    correctly built bundle. The directory containing the .pyd IS searched, so the
#    DLL is staged next to it. That is a copy of OUR OWN library, in the same
#    bundle, not a vendored third-party binary; the alternative (an
#    os.add_dll_directory hook) would need a sitecustomize we do not ship.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "pyyaml-cp311: building with $py"

# Bridge BUILD-only python columns (setuptools, ...) into the DEPS-prefix
# interpreter — same reason as build.sh: they live in CVC_BUILD_PREFIX and
# --no-build-isolation imports them straight off sys.path.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools; print("setuptools", setuptools.__version__, setuptools.__file__)'
if ($LASTEXITCODE -ne 0) { throw "pyyaml-cp311: setuptools not importable from the build prefix" }

# See the header: hand libyaml's location to PyYAML's own PEP-517 config-setting,
# and make the C loader mandatory so a missing yaml.h fails the build instead of
# silently shipping a PyYAML without yaml.CLoader.
$env:PYYAML_FORCE_LIBYAML = '1'

$yamlH = @("$deps\include\yaml.h", "$bld\include\yaml.h") | Where-Object { Test-Path -LiteralPath $_ }
if (-not $yamlH) { throw "pyyaml: yaml.h not found under $deps\include or $bld\include — is the 'yaml' dep in the closure?" }

& $py -c 'import Cython; print("Cython", Cython.__version__)'
if ($LASTEXITCODE -ne 0) { throw "pyyaml: Cython not importable (the sdist ships only _yaml.pyx)" }

# ConvertTo-Json escapes the backslashes for us — hand-building this string is how
# you get a JSON parse error inside the backend instead of a build.
$pyyamlCfg = @{
    include_dirs = @("$deps\include", "$bld\include")
    library_dirs = @("$deps\lib",     "$bld\lib")
} | ConvertTo-Json -Compress
Write-Output "pyyaml: pyyaml_build_config=$pyyamlCfg"

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @('--config-settings', "pyyaml_build_config=$pyyamlCfg") + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "pyyaml-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "pyyaml-cp311: no wheel produced under $wheelhouse" }
Write-Output "pyyaml-cp311: built $($wheel.Name)"

# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree, so installing --prefix into
# the initially-empty per-recipe dir is what keeps the staged tree pure.
& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pyyaml-cp311: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "pyyaml-cp311: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "pyyaml-cp311: staged into $sitePackages"

# Stage yaml.dll beside _yaml.pyd — see header note 2.
$yamlPkgDir = Join-Path $sitePackages 'yaml'
$yamlDll = @("$deps\bin\yaml.dll", "$bld\bin\yaml.dll", "$deps\lib\yaml.dll") |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $yamlDll) {
    throw "pyyaml: yaml.dll not found under $deps\bin or $bld\bin — _yaml.pyd would fail to load at import"
}
Copy-Item -LiteralPath $yamlDll -Destination $yamlPkgDir -Force
Write-Output "pyyaml: staged $yamlDll -> $yamlPkgDir"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import yaml

# The whole reason this column links libyaml. If setup.py had fallen back to the
# pure-Python parser, __with_libyaml__ would be False and CSafeLoader absent —
# an invisible regression for every consumer that asks for the fast loader.
assert yaml.__with_libyaml__ is True, "PyYAML was built WITHOUT libyaml"
from yaml import _yaml
assert _yaml.__file__.endswith((".so", ".pyd", ".dylib")), _yaml.__file__
print("PyYAML", yaml.__version__, "->", _yaml.__file__)

doc = "a: 1\nb: [x, y]\nc: {d: true}\n"
loaded = yaml.load(doc, Loader=yaml.CSafeLoader)
assert loaded == {"a": 1, "b": ["x", "y"], "c": {"d": True}}, loaded

# Round-trip through the C emitter as well as the C parser.
dumped = yaml.dump(loaded, Dumper=yaml.CSafeDumper, default_flow_style=False)
assert yaml.load(dumped, Loader=yaml.CSafeLoader) == loaded

# The unsafe/full loaders must still be the C ones.
assert yaml.CLoader is not None and yaml.CDumper is not None
print("pyyaml round-trip OK (libyaml", ".".join(str(v) for v in _yaml.get_version()) + ")")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "pyyaml-cp311: verification failed ($LASTEXITCODE)" }

Write-Output "pyyaml-cp311: build + verification complete"
