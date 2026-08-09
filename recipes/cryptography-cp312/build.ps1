# recipes/cryptography-cp312/build.ps1 — Windows from-source build of
# cryptography 48.0.0 for the cp312 interpreter column.
#
# This is a real build, not a stub: `source.type` is recipe-wide, so once the
# recipe stopped downloading a wheel the Windows column had to compile too. The
# mechanic is identical to build.sh (`pip wheel --no-build-isolation --no-deps
# --no-index`, then pip-install the result into the staging prefix); MSVC comes
# from _common/env-windows.ps1, which runs Import-CvcMsvcEnv at dot-source time
# and is also where cargo gets link.exe.
#
# DELTAS vs build.sh:
#   * pip's Windows scheme puts console scripts in <prefix>\Scripts, so that is
#     where the maturin executable lives and what PATH has to reach.
#   * no rpath pass — PE has no RUNPATH. libssl-3-x64.dll / libcrypto-3-x64.dll
#     are found the way every other Windows bundle finds its siblings: out of the
#     activated prefix's bin/ on PATH.
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
Write-Output "cryptography-cp312: building with $py"

# Bridge BUILD-only python columns (maturin, cffi, setuptools) into the
# DEPS-prefix interpreter, and put our bin/Scripts ahead of the host's.
$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }
& $py -c 'import setuptools, cffi, maturin; print("setuptools", setuptools.__version__, "cffi", cffi.__version__, "+ maturin backend")'
if ($LASTEXITCODE -ne 0) { throw "cryptography-cp312: build backend not importable from the build prefix" }

# The maturin shim shells out to the maturin EXECUTABLE; resolve it now so a
# missing .data/scripts entry fails with a sentence, not a FileNotFoundError
# from inside pip.
$maturinCmd = Get-Command maturin -ErrorAction SilentlyContinue
if (-not $maturinCmd) { throw "cryptography-cp312: no 'maturin' executable on PATH (maturin-cp312 supplies it in $bld\Scripts)" }
& $maturinCmd.Source --version
if ($LASTEXITCODE -ne 0) { throw "cryptography-cp312: staged maturin failed to run" }

# Pin the Rust toolchain to the one cvcpkg built — a builder's own rustup must
# never compile a cvcpkg artifact.
function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "cryptography-cp312: $Name not on PATH — is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "cryptography-cp312: refusing $Name at $path — outside the cvcpkg prefixes ($bld, $deps)"
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
    Write-Output "cryptography-cp312: CARGO_NET_OFFLINE=true — $env:CARGO_HOME must already hold every crate in Cargo.lock"
} else {
    $env:CARGO_NET_OFFLINE = 'false'
    Write-Output "cryptography-cp312: NOTE - cargo will FETCH crates.io (sdist ships Cargo.lock, no vendored sources)"
}

# Point openssl-sys at OUR OpenSSL. Without OPENSSL_DIR it falls through to
# pkg-config and then a system probe (and on Windows, vcpkg), any of which would
# link a TLS stack cvcpkg neither built nor tracks.
$env:OPENSSL_DIR = $deps
$env:OPENSSL_NO_VENDOR = '1'
$sslHeader = Join-Path $deps 'include\openssl\ssl.h'
if (-not (Test-Path -LiteralPath $sslHeader)) {
    throw "cryptography-cp312: $sslHeader missing — is the openssl dep in the closure?"
}
$opensslExe = Join-Path $deps 'bin\openssl.exe'
if (Test-Path -LiteralPath $opensslExe) {
    $env:CVC_OPENSSL_VERSION = (& $opensslExe version).Split(' ')[1]
}
Write-Output "cryptography-cp312: OPENSSL_DIR=$env:OPENSSL_DIR (version $env:CVC_OPENSSL_VERSION)"

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

$pipArgs = @('-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
             '--no-cache-dir', '--wheel-dir', $wheelhouse) + @($env:CVC_SOURCE_DIR)
& $py @pipArgs
if ($LASTEXITCODE -ne 0) { throw "cryptography-cp312: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "cryptography-cp312: no wheel produced under $wheelhouse" }
Write-Output "cryptography-cp312: built $($wheel.Name)"

# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree, so installing --prefix into
# the initially-empty per-recipe dir is what keeps the staged tree pure.
& $py -m pip install --no-index --no-deps --no-compile --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "cryptography-cp312: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "cryptography-cp312: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}
Write-Output "cryptography-cp312: staged into $sitePackages"

# The staged extension resolves libssl/libcrypto off PATH on Windows; the deps
# prefix is already there from the PATH line above, so the check can just run.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
import hashlib, os, sys, sysconfig

if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import cryptography
from cryptography.hazmat.bindings import _rust

assert _rust.__file__.endswith((".so", ".pyd", ".dylib")), _rust.__file__
print("cryptography", cryptography.__version__, "->", _rust.__file__)

import cffi
print("cffi         :", cffi.__file__)

from cryptography.hazmat.primitives import hashes
h = hashes.Hash(hashes.SHA256())
h.update(b"cvcpkg")
assert h.finalize() == hashlib.sha256(b"cvcpkg").digest()

from cryptography.fernet import Fernet
f = Fernet(Fernet.generate_key())
assert f.decrypt(f.encrypt(b"cvcpkg")) == b"cvcpkg"

from cryptography.hazmat.primitives.asymmetric import ec
key = ec.generate_private_key(ec.SECP256R1())
sig = key.sign(b"cvcpkg", ec.ECDSA(hashes.SHA256()))
key.public_key().verify(sig, b"cvcpkg", ec.ECDSA(hashes.SHA256()))
print("OpenSSL round-trip OK (SHA-256 + Fernet/AES + ECDSA P-256)")

expected = os.environ.get("CVC_OPENSSL_VERSION", "")
linked = None
try:
    from cryptography.hazmat.bindings._rust import openssl as _rust_openssl
    linked = _rust_openssl.openssl_version_text()
except Exception:
    try:
        from cryptography.hazmat.backends.openssl.backend import backend
        linked = backend.openssl_version_text()
    except Exception:
        linked = None

if linked and expected:
    print("linked OpenSSL:", linked, "| prefix openssl:", expected)
    assert expected in linked, (
        "cryptography linked %r, but the cvcpkg prefix ships OpenSSL %s — "
        "openssl-sys picked up a library we do not control" % (linked, expected)
    )
elif linked:
    print("linked OpenSSL:", linked, "(no prefix openssl binary to compare against)")
else:
    print("linked OpenSSL: version text unavailable via this cryptography release;"
          " skipped the provenance comparison")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "cryptography-cp312: verification failed ($LASTEXITCODE)" }

Write-Output "cryptography-cp312: build + verification complete"
