# recipes/openssl/build-wasm.ps1 — cross-compile OpenSSL to wasm via Emscripten.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

# OpenSSL uses perl Configure (not autotools).
# We use Strawberry Perl (fully-featured) from PowerShell directly, but
# OpenSSL's Configurations/unix-checker.pm rejects perls whose
# File::Spec::rel2abs() produces backslash paths.  Patch it out so
# Strawberry Perl can drive Configure for the linux-generic32 target.
$unixChecker = Join-Path $env:CVC_SOURCE_DIR 'Configurations\unix-checker.pm'
if (Test-Path $unixChecker) {
    Set-Content -Path $unixChecker -Value "1;"
}

# Use forward-slash paths for prefix/openssldir since they end up in Makefiles.
$installDir = $env:CVC_INSTALL_DIR -replace '\\','/'
$emscriptenDir = Join-Path $env:CVC_EMSDK_DIR 'upstream\emscripten'

Push-Location $env:CVC_SOURCE_DIR
try {
    $env:CC  = Join-Path $emscriptenDir 'emcc.bat'
    $env:CXX = Join-Path $emscriptenDir 'em++.bat'
    $env:AR  = Join-Path $emscriptenDir 'emar.bat'
    $env:RANLIB = Join-Path $emscriptenDir 'emranlib.bat'

    # Use Strawberry Perl explicitly — env-wasm.ps1 prepends Git's usr/bin
    # to PATH which shadows Strawberry's perl with the minimal MSYS2 perl.
    $strawberryPerl = 'C:\Strawberry\perl\bin\perl.exe'
    if (-not (Test-Path $strawberryPerl)) {
        $strawberryPerl = (Get-Command perl -ErrorAction Stop |
            Where-Object { $_.Source -notmatch 'Git' } |
            Select-Object -First 1).Source
    }

    # Set PERL env with forward slashes so the generated Makefile uses paths
    # that sh.exe won't mangle (backslashes get stripped by MSYS sh).
    $env:PERL = $strawberryPerl -replace '\\','/'

    & $strawberryPerl Configure linux-generic32 `
        "--prefix=$installDir" `
        "--openssldir=$installDir/ssl" `
        no-shared no-asm no-threads no-engine no-dso no-tests no-apps -DNO_FORK
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL Configure failed" }

    # mingw32-make auto-detects sh.exe on PATH and uses it as SHELL.
    # sh.exe strips backslashes from paths (e.g. util\dofile.pl becomes
    # utildofile.pl).  Convert ALL path-separator backslashes in the
    # generated Makefile and configdata.pm to forward slashes.
    # The regex replaces \ between word/dot/colon chars — safe because
    # line continuation \ is always at end-of-line (followed by newline).
    # The colon handles drive letters (C:\src → C:/src).
    foreach ($f in @(
        (Join-Path $env:CVC_SOURCE_DIR 'makefile'),
        (Join-Path $env:CVC_SOURCE_DIR 'configdata.pm')
    )) {
        if (Test-Path $f) {
            (Get-Content $f -Raw) -replace '(?<=[\w.:])\\(?=[\w.])', '/' |
                Set-Content $f -NoNewline
        }
    }

    # The Makefile generates util wrapper scripts (opensslwrap.sh,
    # shlib_wrap.sh, wrap.pl) using PERL + shell quoting that cmd.exe
    # cannot handle.  We don't need them for wasm — neutralise those
    # recipes by replacing them with simple file-creation commands.
    $makefilePath = Join-Path $env:CVC_SOURCE_DIR 'makefile'
    $mf = Get-Content $makefilePath -Raw
    foreach ($target in @('util/opensslwrap.sh', 'util/shlib_wrap.sh', 'util/wrap.pl')) {
        # Match the recipe: target line, then all indented (TAB-prefixed) lines.
        $escaped = [regex]::Escape($target)
        $mf = $mf -replace "(?m)^${escaped}\s*:.*(?:\r?\n\t.*)*", "${target}:`n`ttype nul > `"`$@`""
    }
    Set-Content $makefilePath -Value $mf -NoNewline

    # mingw32-make auto-detects sh.exe on PATH and switches to sh-mode.
    # sh.exe has a much shorter command line limit than cmd.exe, causing
    # "The command line is too long" for AR commands with many .o files.
    # Remove sh.exe directories from PATH and provide minimal shims for
    # utilities that the Makefile needs (touch, rm, chmod).
    $shimDir = Join-Path $env:CVC_SOURCE_DIR '_shims'
    New-Item -ItemType Directory -Path $shimDir -Force | Out-Null
    Set-Content -Path (Join-Path $shimDir 'touch.bat') -Value '@if not exist %~1 type nul > %~1 2>nul'
    Set-Content -Path (Join-Path $shimDir 'rm.bat') -Value '@del /f /q %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9 2>nul & exit /b 0'
    Set-Content -Path (Join-Path $shimDir 'chmod.bat') -Value '@rem no-op on Windows & exit /b 0'

    $savedPath = $env:PATH
    $savedShell = $env:SHELL
    Remove-Item Env:\SHELL -ErrorAction SilentlyContinue
    $env:PATH = ($shimDir + ';' + (
        ($env:PATH -split ';' |
            Where-Object { $_ -and -not (Test-Path (Join-Path $_ 'sh.exe') -ErrorAction SilentlyContinue) }
        ) -join ';'))
    try {
        & mingw32-make -j $env:CVC_JOBS
        if ($LASTEXITCODE -ne 0) { throw "make failed" }

        & mingw32-make install_sw
        if ($LASTEXITCODE -ne 0) { throw "make install_sw failed" }
    }
    finally {
        $env:PATH = $savedPath
        if ($savedShell) { $env:SHELL = $savedShell }
    }
}
finally {
    Pop-Location
}
