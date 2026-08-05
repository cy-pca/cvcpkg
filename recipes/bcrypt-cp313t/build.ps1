# recipes/bcrypt-cp313t/build.ps1 — Windows from-source build of bcrypt 5.0.0 for
# the cp313t interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time
# and is also where cargo gets link.exe.
#
# DELTAS vs build.sh: pip's Windows scheme puts console scripts in
# <prefix>\Scripts, so that is what PATH has to reach for the build-dep columns.
# There is no rpath pass on either platform — _bcrypt has no external NEEDED
# library.
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
Write-Output "bcrypt-cp313t: building with $py"

$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools, setuptools_rust, wheel; print("setuptools", setuptools.__version__, "+ setuptools_rust + wheel")'
if ($LASTEXITCODE -ne 0) { throw "bcrypt-cp313t: build backend not importable from the build prefix" }

# Pin the Rust toolchain to the one cvcpkg built — a builder's own rustup must
# never compile a cvcpkg artifact.
function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "bcrypt-cp313t: $Name not on PATH — is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "bcrypt-cp313t: refusing $Name at $path — outside the cvcpkg prefixes ($bld, $deps)"
    }
    return $path
}
$cargo = Resolve-CvcPrefixTool 'cargo'
$rustc = Resolve-CvcPrefixTool 'rustc'
$env:CARGO = $cargo       # setuptools_rust reads $CARGO
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

if ($env:CVC_CARGO_OFFLINE) {
    $env:CARGO_NET_OFFLINE = 'true'
    Write-Output "bcrypt-cp313t: CARGO_NET_OFFLINE=true — $env:CARGO_HOME must already hold every crate in Cargo.lock"
} else {
    $env:CARGO_NET_OFFLINE = 'false'
    Write-Output "bcrypt-cp313t: NOTE - cargo will FETCH crates.io (sdist ships Cargo.lock, no vendored sources)"
}

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "bcrypt-cp313t: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "bcrypt-cp313t: no wheel produced under $wheelhouse" }
Write-Output "bcrypt-cp313t: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "bcrypt-cp313t: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "bcrypt-cp313t: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "bcrypt-cp313t: staged into $sitePackages"

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import sys, sysconfig

if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import bcrypt
from bcrypt import _bcrypt

assert _bcrypt.__file__.endswith((".so", ".pyd", ".dylib")), _bcrypt.__file__
print("bcrypt", bcrypt.__version__, "->", _bcrypt.__file__)

pw = b"cvcpkg-correct-horse"
hashed = bcrypt.hashpw(pw, bcrypt.gensalt(rounds=4))
assert bcrypt.checkpw(pw, hashed), hashed
assert not bcrypt.checkpw(b"wrong", hashed)

assert hashed.startswith(b"$2b$04$"), hashed
assert len(hashed) == 60, (len(hashed), hashed)
assert bcrypt.hashpw(pw, hashed) == hashed

out = bcrypt.kdf(password=pw, salt=b"cvcpkg-salt", desired_key_bytes=32, rounds=4)
assert isinstance(out, bytes) and len(out) == 32, out
assert bcrypt.kdf(password=pw, salt=b"cvcpkg-salt", desired_key_bytes=32, rounds=4) == out
print("bcrypt round-trip OK")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "bcrypt-cp313t: verification failed ($LASTEXITCODE)" }

Write-Output "bcrypt-cp313t: build + verification complete"
