# recipes/_common/env-windows.ps1 — shared environment for Windows recipe builds.
#
# Dot-sourced by every build.ps1 on Windows.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_BUILD_TYPE)  { $env:CVC_BUILD_TYPE  = 'Release' }
if (-not $env:CVC_LINK)        { $env:CVC_LINK        = 'shared' }
if (-not $env:CVC_JOBS)        { $env:CVC_JOBS        = [Environment]::ProcessorCount }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_BUILD_DIR)   { throw 'CVC_BUILD_DIR must be set' }

$cmakeBuildType = switch ($env:CVC_BUILD_TYPE.ToLower()) {
    'release' { 'Release' }
    'debug'   { 'Debug' }
    default   { 'Release' }
}

$buildSharedLibs = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }

# MSVC runtime library: static link -> /MT (static CRT), shared -> /MD (dynamic CRT)
# The Debug infix goes BEFORE DLL: MultiThreaded[Debug][DLL]
$msvcRuntime = if ($env:CVC_LINK -eq 'static') {
    'MultiThreaded$<$<CONFIG:Debug>:Debug>'
} else {
    'MultiThreaded$<$<CONFIG:Debug>:Debug>DLL'
}

if ($env:CVC_DEPS_PREFIX) {
    $env:CMAKE_PREFIX_PATH = $env:CVC_DEPS_PREFIX
}

# On Windows we ALWAYS build with MSVC (cl.exe).  A prior builder image
# happened to have Strawberry Perl's g++ ahead of cl on PATH, and the
# Ninja generator silently picked it, producing GNU-ar '.a' archives
# with Itanium-mangled symbols that MSVC-mangled downstream consumers
# (protobuf, grpc, etc.) cannot resolve — 100+ "unresolved external"
# link errors.  Force cl.exe here so the mangling and archive format
# stay consistent across the whole dep set.
$env:CC  = 'cl'
$env:CXX = 'cl'

# ── Auto-import MSVC developer environment ──────────────────────────
#
# When the builder daemon is launched as a service (or from a plain
# PowerShell prompt) cl.exe / link.exe / rc.exe are NOT on PATH.
# GitHub Actions runs ilammy/msvc-dev-cmd before invoking us; the
# self-hosted builder does not, so do it ourselves here if needed.
function Import-CvcMsvcEnv {
    if (Get-Command cl.exe -ErrorAction SilentlyContinue) { return }

    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        $vswhere = "$env:ProgramFiles\Microsoft Visual Studio\Installer\vswhere.exe"
    }
    if (-not (Test-Path $vswhere)) {
        throw "cl.exe not on PATH and vswhere.exe not found — install Visual Studio Build Tools or run from a Developer PowerShell."
    }

    $vsRoot = & $vswhere -latest -products '*' `
        -requires 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' `
        -property installationPath 2>$null | Select-Object -First 1
    if (-not $vsRoot) {
        throw "vswhere.exe found no Visual Studio install with the C++ workload."
    }

    $vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) {
        throw "vcvars64.bat not found under $vsRoot"
    }

    # Run vcvars64.bat in a subshell, dump the resulting environment,
    # and import the diff back into our own process.
    $dump = & cmd /c "`"$vcvars`" >nul 2>&1 && set"
    foreach ($line in $dump) {
        if ($line -match '^([^=]+)=(.*)$') {
            $name = $matches[1]
            $val  = $matches[2]
            # Skip a few obviously-per-process env vars we don't want
            # to clobber.
            if ($name -in @('_', 'PROMPT')) { continue }
            [Environment]::SetEnvironmentVariable($name, $val, 'Process')
        }
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "Import-CvcMsvcEnv ran but cl.exe still not on PATH — vcvars64.bat likely failed silently."
    }
}
Import-CvcMsvcEnv

function Invoke-CvcCMakeBuild {
    param([string[]]$ExtraArgs = @())

    # Strip MinGW/MSYS2 dirs from PATH for the duration of the MSVC
    # cmake build.  CMake's find_path()/find_library() default search
    # includes "for each dir in PATH, look in ../include and ../lib" —
    # which means if C:\msys64\mingw64\bin is on Machine PATH the
    # MinGW-w64 headers (stdio.h with __asm__, __builtin_va_list, etc.)
    # get picked up by cl.exe.  Those headers are gcc-only and produce
    # thousands of parse errors under MSVC.  MSVC builds have zero use
    # for anything under C:\msys64\, so drop them entirely here.
    $origPath = $env:PATH
    $filteredPath = ($env:PATH -split ';' |
        Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'
    $env:PATH = $filteredPath

    try {
        $allArgs = @(
            '-G', 'Ninja',
            '-S', $env:CVC_SOURCE_DIR,
            '-B', $env:CVC_BUILD_DIR,
            "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
            "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
            "-DBUILD_SHARED_LIBS=$buildSharedLibs",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            '-DCMAKE_C_COMPILER=cl',
            '-DCMAKE_CXX_COMPILER=cl'
        ) + $ExtraArgs

        & cmake @allArgs
        if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

        & cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
        if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

        & cmake --install $env:CVC_BUILD_DIR
        if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
    } finally {
        $env:PATH = $origPath
    }
}

# ── MSYS2 / MinGW autotools helper ──────────────────────────────────
#
# A handful of dependencies (gmp, mpfr, gsl) have no working native
# MSVC build (upstream autotools is Unix-only, no CMake, GNU-only
# assembly).  For those we build via MinGW-w64 gcc + GNU make driven
# through Git Bash — this produces cdecl-C DLLs that MSVC downstream
# consumers can link against through the mingw-generated import
# library.  When CVC_LINK=static we use the mingw static archive
# directly; it links cleanly from MSVC for pure-C libraries with no
# libgcc/libstdc++ dependencies (which is the case for gmp/mpfr/gsl).
#
# The builder is expected to have Git Bash on PATH (as env-wasm.ps1
# already assumes) plus MSYS2 mingw-w64 gcc + make + m4 + libtool.
# See vm-provisioning docs for the required MSYS2 packages.

function Get-CvcGitBash {
    # Return a bash.exe that has (or can find) mingw-w64 gcc + make +
    # autotools on its PATH.  MSYS2's own bash under C:\msys64 is
    # preferred because it is the environment where our MinGW packages
    # (mingw-w64-x86_64-gcc, make, m4, libtool, autoconf, automake)
    # are installed.  Git Bash is used as a fallback for platforms
    # (e.g. wasm) where no native compilation is needed — those
    # recipes drive emcc/emmake and don't rely on gcc/make being
    # present in the shell.
    foreach ($candidate in @(
        'C:\msys64\usr\bin\bash.exe',
        'C:\tools\msys64\usr\bin\bash.exe',
        "$env:ProgramFiles\Git\usr\bin\bash.exe",
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\usr\bin\bash.exe"
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    $found = Get-Command bash -ErrorAction SilentlyContinue |
             Where-Object { $_.Source -notmatch 'System32' } |
             Select-Object -First 1
    if ($found) { return $found.Source }
    throw 'bash.exe not found (looked under C:\msys64\usr\bin\, Git\usr\bin\, and PATH)'
}

function ConvertTo-CvcMsysPath {
    # Convert C:\foo\bar → /c/foo/bar for autotools scripts.
    param([string]$Path)
    if ($Path -match '^([A-Za-z]):(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest  = $Matches[2] -replace '\\','/'
        return "/$drive$rest"
    }
    return ($Path -replace '\\','/')
}

function Invoke-CvcMsysAutotoolsBuild {
    <#
    .SYNOPSIS
      Configure + make + make install via Git Bash + MinGW gcc.
    .PARAMETER ConfigureArgs
      Extra arguments to pass to ./configure (beyond --prefix + --host).
    .PARAMETER HostTriple
      Cross-compilation host triple.  Defaults to x86_64-w64-mingw32
      (native MinGW-w64 on Windows).
    .PARAMETER Jobs
      Override make -j parallelism.  Defaults to $env:CVC_JOBS.
      Set to 1 for fork-heavy libtool builds that deadlock MSYS2 under
      the SYSTEM account (gmp, mpfr, ...).
    #>
    param(
        [string[]]$ConfigureArgs = @(),
        [string]$HostTriple = 'x86_64-w64-mingw32',
        [int]$Jobs = 0
    )
    if ($Jobs -le 0) { $Jobs = [int]$env:CVC_JOBS }
    if ($Jobs -le 0) { $Jobs = 1 }

    $bash        = Get-CvcGitBash
    $msysPrefix  = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
    $msysSource  = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
    $msysDeps    = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }

    # Force the MinGW-w64 64-bit subsystem so that /mingw64/bin
    # (gcc, make, libtool, autoconf, m4, ...) is on the shell PATH.
    # Without MSYSTEM set, MSYS2's bash defaults to the plain msys
    # environment where gcc is not present.  MSYS_NO_PATHCONV=1
    # prevents MSYS from mangling Windows-style arguments passed to
    # non-MSYS binaries invoked from configure/libtool.
    $env:MSYSTEM         = 'MINGW64'
    $env:MSYS_NO_PATHCONV = '1'
    $env:CHERE_INVOKING  = '1'

    # Clear MSVC compiler env inherited from the outer PowerShell
    # session — env-windows.ps1 sets CC=cl / CXX=cl for Invoke-CvcCMakeBuild,
    # but for the autotools path we need bash's PATH to select the
    # MinGW-w64 gcc from /mingw64/bin.  Autoconf's ./configure would
    # otherwise take CC=cl at face value, produce nonsense build-system
    # detection, and choke on the first Unix-only linker flag (e.g.
    # -lm  →  LINK: cannot open input file 'm.lib').
    Remove-Item Env:CC  -ErrorAction SilentlyContinue
    Remove-Item Env:CXX -ErrorAction SilentlyContinue
    Remove-Item Env:LD  -ErrorAction SilentlyContinue
    Remove-Item Env:AR  -ErrorAction SilentlyContinue
    Remove-Item Env:NM  -ErrorAction SilentlyContinue
    Remove-Item Env:RANLIB -ErrorAction SilentlyContinue
    Remove-Item Env:CFLAGS  -ErrorAction SilentlyContinue
    Remove-Item Env:CXXFLAGS -ErrorAction SilentlyContinue
    Remove-Item Env:LDFLAGS -ErrorAction SilentlyContinue

    $sharedFlags = if ($env:CVC_LINK -eq 'static') {
        '--enable-static --disable-shared'
    } else {
        '--enable-shared --enable-static'
    }

    # Pre-flight: some Windows builders were provisioned with only the
    # MinGW-w64 gcc packages and are missing the autotools bootstrap
    # chain (m4, autoconf, automake, libtool, make).  Symptom: gmp's
    # configure aborts with "No usable m4 in $PATH".  Rather than gate
    # each recipe on a manual reprovision, self-heal by asking pacman
    # to install the --needed set — a no-op when everything is present,
    # a one-time ~20s install otherwise.
    $probe = & $bash -lc 'command -v m4 >/dev/null && command -v libtool >/dev/null && command -v autoconf >/dev/null && command -v automake >/dev/null && command -v make >/dev/null && echo OK'
    if ($probe -notmatch 'OK') {
        Write-Host 'cvcpkg: MSYS2 autotools tools missing on this builder; installing via pacman...'
        & $bash -lc 'pacman -S --noconfirm --needed autoconf automake libtool m4 make patch diffutils'
        if ($LASTEXITCODE -ne 0) { throw 'pacman install of autotools failed' }
    }

    # Build one big command line for bash; the caller-provided extras
    # win over defaults because they come last.
    $extras   = ($ConfigureArgs -join ' ')
    $depsFlag = if ($msysDeps) { "PATH='$msysDeps/bin:'`$PATH" } else { '' }
    $cmd = "$depsFlag cd '$msysSource' && ./configure --prefix='$msysPrefix' --host='$HostTriple' $sharedFlags $extras"
    Write-Host "cvcpkg: bash -lc `"$cmd`""
    & $bash -lc $cmd
    if ($LASTEXITCODE -ne 0) {
        $cfgLog = Join-Path $env:CVC_SOURCE_DIR 'config.log'
        if (Test-Path $cfgLog) {
            Write-Host '--- config.log (last 80 lines) ---'
            Get-Content $cfgLog -Tail 80
        }
        throw "configure failed"
    }

    & $bash -lc "cd '$msysSource' && make -j $Jobs"
    if ($LASTEXITCODE -ne 0) { throw "make failed" }

    & $bash -lc "cd '$msysSource' && make install"
    if ($LASTEXITCODE -ne 0) { throw "make install failed" }

    # Post-process installed libraries so MSVC downstream can link:
    #   libfoo.dll.a  → foo.lib      (import library for shared)
    #   libfoo.a      → foo.lib      (static archive; only used when
    #                                 CVC_LINK=static — safe for
    #                                 pure-C libs without libgcc deps)
    # Copies rather than renames so both mingw-style names and
    # MSVC-style names are present, and downstream find_library() can
    # locate whichever it looks for.
    $installLib = Join-Path $env:CVC_INSTALL_DIR 'lib'
    if (Test-Path $installLib) {
        foreach ($f in Get-ChildItem -Path $installLib -File -Filter 'lib*.dll.a' -ErrorAction SilentlyContinue) {
            $stem = $f.Name.Substring(3, $f.Name.Length - 3 - 6)   # strip "lib" prefix + ".dll.a" suffix
            $dest = Join-Path $installLib ($stem + '.lib')
            if (-not (Test-Path $dest)) { Copy-Item -Force $f.FullName $dest }
        }
        if ($env:CVC_LINK -eq 'static') {
            foreach ($f in Get-ChildItem -Path $installLib -File -Filter 'lib*.a' -ErrorAction SilentlyContinue) {
                if ($f.Name -like '*.dll.a') { continue }
                $stem = $f.Name.Substring(3, $f.Name.Length - 3 - 2)  # strip "lib" prefix + ".a" suffix
                $dest = Join-Path $installLib ($stem + '.lib')
                if (-not (Test-Path $dest)) { Copy-Item -Force $f.FullName $dest }
            }
        }
    }
}

Write-Host "-- env-windows.ps1 loaded --"
Write-Host "  BUILD_TYPE=$cmakeBuildType  LINK=$env:CVC_LINK  JOBS=$env:CVC_JOBS"
