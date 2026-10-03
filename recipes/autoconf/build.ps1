# recipes/autoconf/build.ps1 — build GNU Autoconf on Windows, for the MSYS subsystem.
#
# Autoconf is a build-time host tool only: Perl scripts plus m4 macro trees that
# run inside MSYS2's bash, driving the MSYS perl of the `msys2` bootstrap recipe
# and the cvcpkg m4. Invoke-CvcMsysHostToolBuild runs this recipe's own build.sh
# under that shell -- so the Windows bundle gets the same post-install
# relocation as unix (the tools derive their prefix from $0 instead of baking
# the ephemeral build prefix; see build.sh).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysHostToolBuild -Require make, m4, perl

$autoconf = Join-Path $env:CVC_INSTALL_DIR 'bin\autoconf'
if (-not (Test-Path -LiteralPath $autoconf)) { throw "autoconf build produced no $autoconf" }
$out = & (Get-CvcGitBash) -lc "'$(ConvertTo-CvcMsysPath $autoconf)' --version"
if ($LASTEXITCODE -ne 0) { throw "installed autoconf failed to run (exit $LASTEXITCODE)" }
Write-Host ($out | Select-Object -First 1)
