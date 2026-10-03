# recipes/make/build.ps1 — build GNU Make on Windows, for the MSYS subsystem.
#
# make is a build-time host tool only (no Windows library produced). It drives
# every autotools build inside MSYS2's bash, so it is an MSYS-subsystem binary
# compiled by the MSYS gcc of the `msys2` bootstrap recipe. That bootstrap
# deliberately ships NO make (make comes from this recipe, nowhere else), so
# build.sh bootstraps it with GNU Make's own make-less `build.sh` and installs
# with the fresh binary. Invoke-CvcMsysHostToolBuild runs this recipe's build.sh
# under that shell (see recipes/_common/env-windows.ps1).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysHostToolBuild -Require gcc

# Smoke-run the installed binary (capture first: piping straight into
# Select-Object would early-close the pipe and clobber the exit code).
$make = Join-Path $env:CVC_INSTALL_DIR 'bin\make.exe'
if (-not (Test-Path -LiteralPath $make)) { throw "make build produced no $make" }
$out = & (Get-CvcGitBash) -lc "'$(ConvertTo-CvcMsysPath $make)' --version"
if ($LASTEXITCODE -ne 0) { throw "installed make failed to run (exit $LASTEXITCODE)" }
Write-Host ($out | Select-Object -First 1)
