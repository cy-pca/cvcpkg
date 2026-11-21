# recipes/minizip/build.ps1 -- classic minizip on Windows (see build.sh).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$zlibSrc = $env:CVC_SOURCE_DIR -replace '\\', '/'
$env:CVC_SOURCE_DIR = $scriptDir
try {
    Invoke-CvcCMakeBuild @("-DZLIB_SOURCE_DIR=$zlibSrc")
} finally {
    $env:CVC_SOURCE_DIR = $zlibSrc
}
