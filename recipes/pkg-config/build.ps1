# recipes/pkg-config/build.ps1 — build pkgconf (the pkg-config replacement)
# on Windows via Meson + MSVC.
#
# Was: autotools under MSYS2/MinGW64 through Get-CvcGitBash. That path is not
# viable here — Git for Windows ships bash but no C compiler, so `./configure`
# fails with "C compiler cannot create executables" and the recipe advertised a
# windows build it could never produce. pkgconf carries a first-class meson
# build, MSVC compiles it cleanly, and every other native windows recipe
# already goes through the shared meson/cmake helpers. Use that instead of
# requiring a second toolchain.
#
# This matters beyond pkgconf itself: numpy's meson resolves BLAS with
# pkgconfig and `system` only (never cmake), so without a working pkg-config in
# the prefix numpy-cp312 cannot find cvcpkg's OpenBLAS at all and fails with
# "No BLAS library detected!".
#
# Symlinks are unreliable on Windows, so pkgconf.exe is COPIED to
# pkg-config.exe — meson and cmake look for either name.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# meson is a Python program and its launcher resolves `python` off PATH. On a
# stock Windows box that name is the Microsoft Store *alias stub*, so meson
# setup dies with "Python was not found; run without arguments to install from
# the Microsoft Store" before it ever configures anything. Put the dependency
# prefix's own interpreter (and its Scripts\) first so meson runs on the
# hermetic python, not a Store shim or whatever else is on PATH.
$_deps = if ($env:CVC_DEPS_PREFIX) { $env:CVC_DEPS_PREFIX } else { $env:CVC_INSTALL_DIR }
if ($_deps -and (Test-Path (Join-Path $_deps 'python.exe'))) {
    $env:PATH = "$_deps;$(Join-Path $_deps 'Scripts');$(Join-Path $_deps 'bin');$env:PATH"
}

# No -Dtests: pkgconf 3.0.4's meson does not define that option and meson
# rejects unknown options outright ("ERROR: Unknown options: \"tests\"").
Invoke-CvcMesonBuild

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$pkgconf = Join-Path $bin 'pkgconf.exe'
if (-not (Test-Path $pkgconf)) {
    throw "pkg-config: meson install produced no $pkgconf"
}
Copy-Item $pkgconf (Join-Path $bin 'pkg-config.exe') -Force

& (Join-Path $bin 'pkg-config.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'pkg-config: staged pkg-config.exe does not run' }
