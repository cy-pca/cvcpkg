# recipes/zlib/build-wasi.ps1 — cross-compile zlib to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

# Patch: Remove the shared library target that conflicts on wasi (static-only).
$cml = Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt'
$content = Get-Content $cml -Raw
$content = $content -replace '(?m)^add_library\(zlib SHARED[^\n]*\n', ''
$content = $content -replace 'set_target_properties\(zlib zlibstatic', 'set_target_properties(zlibstatic'
$content = $content -replace 'install\(TARGETS zlib zlibstatic', 'install(TARGETS zlibstatic'
$content = $content -replace '(?m)^\s*target_include_directories\(zlib [^\n]*\n', ''
$content = $content -replace '(?m)^\s*set_target_properties\(zlib [^\n]*\n', ''
$content = $content -replace 'target_link_libraries\(example zlib\)', 'target_link_libraries(example zlibstatic)'
$content = $content -replace 'target_link_libraries\(minigzip zlib\)', 'target_link_libraries(minigzip zlibstatic)'
$content = $content -replace 'target_link_libraries\(example64 zlib\)', 'target_link_libraries(example64 zlibstatic)'
$content = $content -replace 'target_link_libraries\(minigzip64 zlib\)', 'target_link_libraries(minigzip64 zlibstatic)'
Set-Content $cml $content -NoNewline

Invoke-CvcWasiCMakeBuild @(
    '-DZLIB_BUILD_EXAMPLES=OFF',
    "-DINSTALL_PKGCONFIG_DIR=$env:CVC_INSTALL_DIR\lib\pkgconfig"
)
