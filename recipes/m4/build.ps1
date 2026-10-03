# recipes/m4/build.ps1 — build GNU m4 on Windows, for the MSYS subsystem.
#
# m4 is a build-time host tool only (no Windows library produced). It runs
# inside MSYS2's bash, where autoconf/automake/gmp's configure call it, so it
# is built the way MSYS2 builds its own m4: an MSYS-subsystem binary compiled
# by the MSYS gcc of the `msys2` bootstrap recipe, with the cvcpkg `make`.
# Invoke-CvcMsysHostToolBuild runs this recipe's own build.sh under that shell
# (see recipes/_common/env-windows.ps1).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysHostToolBuild -Require gcc, make

# Smoke-run the installed binary (capture first: piping straight into
# Select-Object would early-close the pipe and clobber the exit code).
$m4 = Join-Path $env:CVC_INSTALL_DIR 'bin\m4.exe'
if (-not (Test-Path -LiteralPath $m4)) { throw "m4 build produced no $m4" }
$out = & (Get-CvcGitBash) -lc "'$(ConvertTo-CvcMsysPath $m4)' --version"
if ($LASTEXITCODE -ne 0) { throw "installed m4 failed to run (exit $LASTEXITCODE)" }
Write-Host ($out | Select-Object -First 1)
