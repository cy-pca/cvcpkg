# recipes/qt6/build.ps1 — build Qt 6 Base from source on Windows.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DINPUT_opengl=yes',
    '-DQT_BUILD_EXAMPLES=OFF',
    '-DQT_BUILD_TESTS=OFF',
    '-DQT_BUILD_BENCHMARKS=OFF',
    '-DFEATURE_icu=OFF',
    '-DFEATURE_sql_mysql=OFF',
    '-DFEATURE_sql_psql=OFF'
)
