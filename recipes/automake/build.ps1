# recipes/automake/build.ps1 — build GNU Automake on Windows, for the MSYS subsystem.
#
# Automake is a build-time host tool only: Perl scripts that run inside MSYS2's
# bash under the MSYS perl of the `msys2` bootstrap recipe. Its configure proves
# the toolchain by running the cvcpkg autoconf (and so the cvcpkg m4).
# Invoke-CvcMsysHostToolBuild runs this recipe's own build.sh under that shell,
# so the Windows bundle gets the same help2man stub and self-relative @INC
# relocation as unix (see build.sh).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysHostToolBuild -Require make, m4, perl, autoconf

$automake = Join-Path $env:CVC_INSTALL_DIR 'bin\automake'
if (-not (Test-Path -LiteralPath $automake)) { throw "automake build produced no $automake" }
$out = & (Get-CvcGitBash) -lc "'$(ConvertTo-CvcMsysPath $automake)' --version"
if ($LASTEXITCODE -ne 0) { throw "installed automake failed to run (exit $LASTEXITCODE)" }
Write-Host ($out | Select-Object -First 1)
