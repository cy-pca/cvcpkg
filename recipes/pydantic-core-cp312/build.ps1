# recipes/pydantic-core-cp312/build.ps1 — Windows from-source build of
# pydantic-core 2.46.4 for the cp312 interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time
# and is also where cargo gets link.exe.
#
# DELTAS vs build.sh: pip's Windows scheme puts console scripts in
# <prefix>\Scripts, so that is where the maturin executable lives and what PATH
# has to reach. There is no rpath pass on either platform — this extension has
# no external NEEDED library.
#
# HERMETICITY: same caveat as build.sh — cargo fetches crates.io, because the
# sdist ships Cargo.lock without vendored sources.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe/link.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "pydantic-core-cp312: building with $py"

$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import maturin; print("maturin backend", maturin.__file__)'
if ($LASTEXITCODE -ne 0) { throw "pydantic-core-cp312: maturin backend not importable from the build prefix" }

$maturinCmd = Get-Command maturin -ErrorAction SilentlyContinue
if (-not $maturinCmd) { throw "pydantic-core-cp312: no 'maturin' executable on PATH (maturin-cp312 supplies it in $bld\Scripts)" }
& $maturinCmd.Source --version
if ($LASTEXITCODE -ne 0) { throw "pydantic-core-cp312: staged maturin failed to run" }

# Pin the Rust toolchain to the one cvcpkg built — a builder's own rustup must
# never compile a cvcpkg artifact.
function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "pydantic-core-cp312: $Name not on PATH — is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "pydantic-core-cp312: refusing $Name at $path — outside the cvcpkg prefixes ($bld, $deps)"
    }
    return $path
}
$cargo = Resolve-CvcPrefixTool 'cargo'
$rustc = Resolve-CvcPrefixTool 'rustc'
$env:CARGO = $cargo
$env:RUSTC = $rustc
& $cargo --version
& $rustc --version

# Keep cargo's state inside the build tree rather than %USERPROFILE%\.cargo.
$env:CARGO_HOME = if ($env:CVC_CARGO_HOME) { $env:CVC_CARGO_HOME }
                  else { Join-Path $env:CVC_BUILD_DIR 'cargo-home' }
$env:RUSTUP_HOME = Join-Path $env:CVC_BUILD_DIR 'rustup-home'
Remove-Item Env:\RUSTUP_TOOLCHAIN -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $env:CARGO_HOME, $env:RUSTUP_HOME | Out-Null
$env:CARGO_TARGET_DIR = Join-Path $env:CVC_BUILD_DIR 'cargo-target'
$env:MATURIN_NO_INSTALL_RUST = '1'

if ($env:CVC_CARGO_OFFLINE) {
    $env:CARGO_NET_OFFLINE = 'true'
    Write-Output "pydantic-core-cp312: CARGO_NET_OFFLINE=true — $env:CARGO_HOME must already hold every crate in Cargo.lock"
} else {
    $env:CARGO_NET_OFFLINE = 'false'
    Write-Output "pydantic-core-cp312: NOTE - cargo will FETCH crates.io (sdist ships Cargo.lock, no vendored sources)"
}

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "pydantic-core-cp312: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "pydantic-core-cp312: no wheel produced under $wheelhouse" }
Write-Output "pydantic-core-cp312: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pydantic-core-cp312: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "pydantic-core-cp312: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "pydantic-core-cp312: staged into $sitePackages"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import sys, sysconfig

if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import pydantic_core
from pydantic_core import _pydantic_core, core_schema, SchemaValidator, ValidationError

assert _pydantic_core.__file__.endswith((".so", ".pyd", ".dylib")), _pydantic_core.__file__
print("pydantic_core", pydantic_core.__version__, "->", _pydantic_core.__file__)

import typing_extensions
print("typing_extensions:", typing_extensions.__file__)

v = SchemaValidator(core_schema.typed_dict_schema({
    "name": core_schema.typed_dict_field(core_schema.str_schema()),
    "count": core_schema.typed_dict_field(core_schema.int_schema()),
}))
assert v.validate_python({"name": "cvcpkg", "count": "7"}) == {"name": "cvcpkg", "count": 7}
try:
    v.validate_python({"name": "cvcpkg", "count": "not-an-int"})
except ValidationError:
    pass
else:
    raise AssertionError("validator accepted a bad int — the Rust core is not doing the work")
print("pydantic_core round-trip OK")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "pydantic-core-cp312: verification failed ($LASTEXITCODE)" }

Write-Output "pydantic-core-cp312: build + verification complete"
