# recipes/gperf/build.ps1 — build GNU gperf on Windows with MSVC.
#
# gperf 3.1 ships only an autotools build, which does not run under MSVC, so
# compile its sources with cl.exe directly, as recipes/lua does.  The only
# configure result the sources read is src/config.h's HAVE_DYNAMIC_ARRAY (C++
# stack arrays of variable size); MSVC has none, so it stays undefined.
#
# fontconfig runs gperf at meson setup to generate src/fcobjshash.h.  Before
# this build, Windows had no cvcpkg gperf and meson fell back to fontconfig's
# subprojects/gperf.wrap: a git clone of an unpinned branch during the build.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$src = $env:CVC_SOURCE_DIR
$obj = Join-Path $env:CVC_BUILD_DIR 'gperf'
$install = $env:CVC_INSTALL_DIR
foreach ($d in @($obj, "$install\bin", "$install\share\man\man1", "$install\share\info",
                 "$install\share\doc\gperf")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

Set-Content -Encoding ascii -Path (Join-Path $obj 'config.h') -Value @(
    '/* config.h for MSVC: no variable-size stack arrays, so HAVE_DYNAMIC_ARRAY is undefined. */'
)

$sources = @(
    "$src\lib\getline.cc", "$src\lib\hash.cc", "$src\lib\getopt.c", "$src\lib\getopt1.c"
) + (Get-ChildItem "$src\src\*.cc" | ForEach-Object { $_.FullName })

# /MD: the shared CRT, as every other cvcpkg Windows bundle.
Push-Location $obj
try {
    & cl /nologo /O2 /EHsc /MD /W1 "/I$obj" "/I$src\lib" $sources /Fe:gperf.exe
    if ($LASTEXITCODE -ne 0) { throw 'gperf build failed' }
} finally {
    Pop-Location
}

# The same files the autotools install puts in place (see build.sh); the
# tarball ships the manual pre-built.
Copy-Item -Force (Join-Path $obj 'gperf.exe') "$install\bin\gperf.exe"
Copy-Item -Force "$src\doc\gperf.1" "$install\share\man\man1\gperf.1"
Copy-Item -Force "$src\doc\gperf.info" "$install\share\info\gperf.info"
Copy-Item -Force "$src\doc\gperf.html" "$install\share\doc\gperf\gperf.html"

# Smoke test: generate a lookup for two keywords.
$kw = Join-Path $obj 'smoke.gperf'
Set-Content -Encoding ascii -Path $kw -Value @('family', 'style')
$out = & "$install\bin\gperf.exe" -L ANSI-C $kw
if ($LASTEXITCODE -ne 0 -or -not ($out -match 'in_word_set')) { throw 'gperf smoke test failed' }
& "$install\bin\gperf.exe" --version | Select-Object -First 1
