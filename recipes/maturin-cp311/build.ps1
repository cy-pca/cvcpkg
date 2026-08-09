# recipes/maturin-cp311/build.ps1 — Windows from-source build of maturin 1.14.1
# for the cp311 interpreter column.
#
# Same contract as build.sh (see there for why maturin is in the graph and how its
# bootstrap backend works): pip wheel --no-build-isolation --no-deps --no-index,
# then pip-install the result into the staging prefix. MSVC comes from
# _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time; cargo
# uses link.exe from that same environment.
#
# DELTA vs build.sh: pip's Windows install scheme puts the compiled maturin
# executable in <prefix>\Scripts, not <prefix>\bin, so both the PATH we export
# for consumers and the verification below look there.
#
# HERMETICITY: identical caveat — cargo fetches crates.io because the sdist ships
# Cargo.lock without vendored sources.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe/link.exe on PATH, CVC_* checks
. "$scriptDir\..\_common\python-wheel.ps1"   # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "maturin-cp311: building with $py"

# Our cargo/rustc ahead of anything the builder has; Scripts\ too, because that is
# where pip put the console scripts of the build-dep columns.
$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools, setuptools_rust, wheel; print("setuptools", setuptools.__version__, "+ setuptools_rust + wheel")'
if ($LASTEXITCODE -ne 0) { throw "maturin-cp311: build backend not importable from the build prefix" }

# Pin the toolchain to the one cvcpkg built — a builder's own rustup must never
# compile a cvcpkg artifact.
function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "maturin-cp311: $Name not on PATH — is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "maturin-cp311: refusing $Name at $path — outside the cvcpkg prefixes ($bld, $deps)"
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
$env:MATURIN_NO_INSTALL_RUST = '1'

if ($env:CVC_CARGO_OFFLINE) {
    $env:CARGO_NET_OFFLINE = 'true'
    Write-Output "maturin-cp311: CARGO_NET_OFFLINE=true — $env:CARGO_HOME must already hold every crate in Cargo.lock"
} else {
    $env:CARGO_NET_OFFLINE = 'false'
    Write-Output "maturin-cp311: NOTE - cargo will FETCH crates.io (sdist ships Cargo.lock, no vendored sources)"
}

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "maturin-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "maturin-cp311: no wheel produced under $wheelhouse" }
Write-Output "maturin-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "maturin-cp311: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "maturin-cp311: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "maturin-cp311: staged into $sitePackages"

$maturinExe = Join-Path $env:CVC_INSTALL_DIR 'Scripts\maturin.exe'
if (-not (Test-Path -LiteralPath $maturinExe)) {
    throw "maturin-cp311: no maturin.exe staged at $maturinExe"
}
& $maturinExe --version
if ($LASTEXITCODE -ne 0) { throw "maturin-cp311: staged maturin.exe failed to run" }

$env:MATURIN_EXE = $maturinExe
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import os, subprocess, sys, sysconfig

if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import maturin
for hook in ("build_wheel", "build_sdist", "get_requires_for_build_wheel",
             "prepare_metadata_for_build_wheel"):
    assert hasattr(maturin, hook), "maturin backend is missing " + hook
print("maturin shim ->", maturin.__file__)

from maturin import bootstrap
os.environ["MATURIN_NO_INSTALL_RUST"] = "1"
assert bootstrap.get_requires_for_build_wheel() == [], \
    "maturin bootstrap still wants an out-of-graph rust installer"

exe = os.environ["MATURIN_EXE"]
out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True).stdout
print("maturin exe  ->", exe, "|", out.strip())
assert "maturin" in out, out
print("maturin round-trip OK")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "maturin-cp311: verification failed ($LASTEXITCODE)" }

Write-Output "maturin-cp311: build + verification complete"
