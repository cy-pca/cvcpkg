# recipes/libtool/build.ps1 — build GNU Libtool on Windows, for the MSYS subsystem.
#
# Libtool is a build-time host tool only. Its scripts (and libltdl) run inside
# MSYS2's bash, so it is built the way MSYS2 builds its own: by the MSYS gcc of
# the `msys2` bootstrap recipe, with the cvcpkg make and m4. Invoke-CvcMsysHostToolBuild
# runs this recipe's own build.sh (which touches the autotools timestamps so
# make never tries to regenerate them) under that shell.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMsysHostToolBuild -Require gcc, make, m4

$libtool = Join-Path $env:CVC_INSTALL_DIR 'bin\libtool'
if (-not (Test-Path -LiteralPath $libtool)) { throw "libtool build produced no $libtool" }
$out = & (Get-CvcGitBash) -lc "'$(ConvertTo-CvcMsysPath $libtool)' --version"
if ($LASTEXITCODE -ne 0) { throw "installed libtool failed to run (exit $LASTEXITCODE)" }
Write-Host ($out | Select-Object -First 1)
