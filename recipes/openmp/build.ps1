# recipes/openmp/build.ps1 -- LLVM's OpenMP runtime (libomp) on Windows (MSVC).
#
# Why this exists on Windows: MSVC's own OpenMP runtimes are no use to a
# consumer that ships binaries.
#   * /openmp links vcomp140.dll, which implements OpenMP 2.0 only. libcvc's
#     voxels.cpp needs OpenMP 3.0 (unsigned loop indices, min/max reductions),
#     so cvc is compiled with -openmp:llvm instead.
#   * -openmp:llvm links libomp140.x86_64.dll, which Microsoft documents as NOT
#     redistributable ("the required libomp DLLs aren't redistributable"). It is
#     not in VC\Redist, so a cvc.dll importing it loads only where Visual Studio
#     is installed.
# This is LLVM's own libomp (Apache-2.0 WITH LLVM-exception, redistributable),
# built here with cl so a consumer can link it instead. See recipe.yaml's notes
# for the import-library name and the exact link line.
#
# Build tools: CMake and Ninja (cvcpkg build deps), cl + link + ml64 from the
# MSVC toolset (z_Windows_NT-586_asm.asm is MASM), and Python 3 for upstream's
# generators (message-converter.py writes kmp_i18n_*.inc, generate-def.py the
# export .def files). openmp/runtime/cmake/config-ix.cmake has
#   find_package(Python3 REQUIRED COMPONENTS Interpreter)
# so there is no build without one; it comes from cvcpkg's python311 (see
# recipe.yaml). No Perl: the runtime's Perl tools were rewritten in Python
# before LLVM 21.
#
# Kept runnable under Windows PowerShell 5.1 (winhost-run-job.ps1 falls back to
# it when a host has no pwsh): no ternaries, no `&&`, two-argument Join-Path.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is ~10x slower with the progress bar
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$llvmVer = '21.1.8'
# Same pin as build.sh: the LLVM common cmake/ modules (see the staging step).
$cmakeTarballSha256 = '85735f20fd8c81ecb0a09abb0c267018475420e93b65050cc5b7634eab744de9'
$cmakeTarballUrl = "https://github.com/llvm/llvm-project/releases/download/llvmorg-$llvmVer/cmake-$llvmVer.src.tar.xz"

# Name of the second, collision-free copy of the import library. Upstream's
# lib\libomp.lib has the same file name as MSVC's own libomp.lib (the import
# library for libomp140.x86_64.dll that -openmp:llvm requests through
# /DEFAULTLIB:libomp.lib). See recipe.yaml, "Import library".
$distinctImplib = 'libomp-llvm.lib'

# -- Python ---------------------------------------------------------------------
# python311 is a depends.build entry, so it is staged into the build prefix
# (CVC_BUILD_PREFIX); `install-deps` + `build --no-deps` puts it in the deps
# prefix instead. Windows CPython installs a bare python.exe at the prefix
# ROOT (no bin\, no version suffix). Never fall back to a python on PATH: on a
# stock Windows box that name is the Microsoft Store alias stub, and on a
# runner it is whatever the image ships.
$python = $null
foreach ($root in @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ } | Select-Object -Unique) {
    $cand = Join-Path $root 'python.exe'
    if (Test-Path -LiteralPath $cand) { $python = $cand; break }
}
if (-not $python) {
    throw ("openmp: no python.exe in CVC_BUILD_PREFIX ($env:CVC_BUILD_PREFIX) or CVC_DEPS_PREFIX " +
           "($env:CVC_DEPS_PREFIX). libomp's build runs Python generators; recipe.yaml declares " +
           "python311 under depends.build for windows -- install it into the prefix " +
           "(cvcpkg install-deps recipes/openmp) or build with --with-deps.")
}
$pyVer = & $python -c "import sys; print(sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "openmp: $python does not run" }
Write-Host "openmp: Python $pyVer at $python"

# -- Stage the monorepo layout ----------------------------------------------------
# openmp/CMakeLists.txt does a plain
#   set(LLVM_COMMON_CMAKE_UTILS ${CMAKE_CURRENT_SOURCE_DIR}/../cmake)
# (not overridable with -D), so LLVM's common cmake/ tree must sit in a
# directory literally named `cmake` NEXT TO the openmp source. A cvcpkg
# `source:` block fetches one tarball; the second is fetched here, sha256-pinned,
# exactly as build.sh does.
$stage = Join-Path $env:CVC_BUILD_DIR 'llvm-src'
if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -Recurse -Path $env:CVC_SOURCE_DIR -Destination (Join-Path $stage 'openmp')

$tarball = Join-Path $env:CVC_BUILD_DIR "cmake-$llvmVer.src.tar.xz"
# Windows PowerShell 5.1 on an older .NET may not offer TLS 1.2 by default;
# GitHub requires it. Harmless under pwsh 7.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Write-Host "openmp: downloading $cmakeTarballUrl ..."
$retries = 5
for ($i = 1; $i -le $retries; $i++) {
    try {
        Invoke-WebRequest -Uri $cmakeTarballUrl -OutFile $tarball -UseBasicParsing
        break
    } catch {
        if ($i -eq $retries) { throw }
        Write-Host "  retry $i/$retries ..."
        Start-Sleep -Seconds 3
    }
}
$actual = (Get-FileHash -LiteralPath $tarball -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $cmakeTarballSha256) {
    throw "openmp: cmake-$llvmVer.src.tar.xz sha256 mismatch (expected $cmakeTarballSha256, got $actual)"
}

# Extract with cvcpkg's cmake, not Windows' tar.exe: the bsdtar that ships with
# Windows Server 2022 / Windows 10 has no xz support, while CMake bundles
# libarchive with liblzma. `cmake -E tar` has no --strip-components, so unpack
# into a scratch dir and move the single top-level directory into place.
$unpack = Join-Path $env:CVC_BUILD_DIR 'cmake-unpack'
if (Test-Path -LiteralPath $unpack) { Remove-Item -Recurse -Force -LiteralPath $unpack }
New-Item -ItemType Directory -Force -Path $unpack | Out-Null
Push-Location $unpack
try {
    & cmake -E tar xf $tarball
    if ($LASTEXITCODE -ne 0) { throw "openmp: cmake -E tar xf $tarball failed" }
} finally {
    Pop-Location
}
$top = @(Get-ChildItem -LiteralPath $unpack -Directory)
if ($top.Count -ne 1) {
    throw "openmp: expected one top-level directory in cmake-$llvmVer.src.tar.xz, found $($top.Count)"
}
Move-Item -LiteralPath $top[0].FullName -Destination (Join-Path $stage 'cmake')

# -- Configure, build, install ------------------------------------------------------
$ompBuild = Join-Path $env:CVC_BUILD_DIR 'omp-build'
$cmakeArgs = @(
    '-G', 'Ninja',
    '-S', (Join-Path $stage 'openmp'),
    '-B', $ompBuild,
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DCMAKE_C_COMPILER=cl',
    '-DCMAKE_CXX_COMPILER=cl',
    # /MD for the shared variant, /MT for static (env-windows.ps1). libomp's
    # C API passes no CRT objects across the DLL boundary, so a /MT libomp.dll
    # is safe under a /MD consumer and spares the static variant a
    # VCRUNTIME140.dll dependency.
    "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
    "-DPython3_EXECUTABLE=$python",
    '-DOPENMP_STANDALONE_BUILD=ON',
    # ALWAYS shared, whatever CVC_LINK says: upstream has no static libomp on
    # Windows (runtime/CMakeLists.txt: "Static libraries requested but not
    # available on Windows"). The static variant therefore ships the DLL too.
    '-DLIBOMP_ENABLE_SHARED=ON',
    # Explicitly off: ON would name the DLL libomp140.x86_64.dll -- Microsoft's
    # file name -- and a second, different binary under that name is exactly
    # the confusion this recipe exists to remove.
    '-DOPENMP_MSVC_NAME_SCHEME=OFF',
    # Runtime only, as in build.sh. On Windows upstream already defaults
    # libomptarget, the OMPT tools and OMPD off; pinned so a default change
    # upstream cannot grow the bundle silently.
    '-DOPENMP_ENABLE_LIBOMPTARGET=OFF',
    '-DOPENMP_ENABLE_OMPT_TOOLS=OFF',
    '-DLIBOMP_OMPD_SUPPORT=OFF'
)

# Strip MSYS2 from PATH for the MSVC build (same reason as
# Invoke-CvcCMakeBuild: CMake's find_* would otherwise pick up MinGW headers).
$origPath = $env:PATH
$env:PATH = ($env:PATH -split ';' |
    Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'
try {
    & cmake @cmakeArgs
    if ($LASTEXITCODE -ne 0) { throw 'cmake configure failed' }
    & cmake --build $ompBuild -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw 'cmake build failed' }
    & cmake --install $ompBuild
    if ($LASTEXITCODE -ne 0) { throw 'cmake install failed' }
} finally {
    $env:PATH = $origPath
}

# -- What did upstream install? -------------------------------------------------------
$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include'
Write-Host '-- openmp: installed tree --'
Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -File | ForEach-Object {
    Write-Host ("  {0}  ({1} bytes)" -f $_.FullName.Substring($env:CVC_INSTALL_DIR.Length).TrimStart('\', '/'), $_.Length)
}

$dll = Join-Path $bin 'libomp.dll'
$implib = Join-Path $lib 'libomp.lib'
foreach ($required in @($dll, $implib, (Join-Path $inc 'omp.h'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "openmp: $required was not installed"
    }
}
$msvcNamed = @(Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -File -Filter 'libomp140*')
if ($msvcNamed.Count -gt 0) {
    throw "openmp: installed a file under Microsoft's libomp140 name: $($msvcNamed[0].FullName)"
}

# The collision-free copy (see $distinctImplib above). Same bytes as libomp.lib.
$distinct = Join-Path $lib $distinctImplib
Copy-Item -LiteralPath $implib -Destination $distinct -Force

# Every import library in lib\ must import from libomp.dll and nothing else --
# the whole point is that nothing here can bind a consumer to libomp140.
foreach ($l in @(Get-ChildItem -LiteralPath $lib -File -Filter '*.lib')) {
    $hdr = & dumpbin /nologo /headers $l.FullName
    if ($LASTEXITCODE -ne 0) { throw "openmp: dumpbin /headers $($l.Name) failed" }
    $dllNames = @($hdr | Select-String -Pattern 'DLL name\s*:\s*(\S+)' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } | Sort-Object -Unique)
    $byOrdinal = @($hdr | Select-String -Pattern 'Name type\s*:\s*ordinal').Count
    $byName = @($hdr | Select-String -Pattern 'Name type\s*:\s*(name|undecorate|no prefix)').Count
    Write-Host ("openmp: lib\{0}: imports from [{1}]; {2} by name, {3} by ordinal" -f $l.Name, ($dllNames -join ', '), $byName, $byOrdinal)
    if ($dllNames.Count -ne 1 -or $dllNames[0] -ne 'libomp.dll') {
        throw "openmp: lib\$($l.Name) imports from [$($dllNames -join ', ')], expected only libomp.dll"
    }
}
Write-Host '-- openmp: bin\libomp.dll dependents --'
& dumpbin /nologo /dependents $dll
if ($LASTEXITCODE -ne 0) { throw 'openmp: dumpbin /dependents libomp.dll failed' }

# -- Smoke test: the consumer's link line ------------------------------------------------
# Compile the way libcvc compiles cvc (cl -openmp:llvm, with OpenMP 3.0
# constructs that MSVC's /openmp rejects), link the import library by FULL PATH
# with /NODEFAULTLIB:libomp.lib, and prove the result imports libomp.dll and
# neither libomp140.x86_64.dll nor vcomp. Built under CVC_BUILD_DIR, never the
# install dir: everything in the install dir ships.
$smoke = Join-Path $env:CVC_BUILD_DIR 'omp-smoke'
if (Test-Path -LiteralPath $smoke) { Remove-Item -Recurse -Force -LiteralPath $smoke }
New-Item -ItemType Directory -Force -Path $smoke | Out-Null
$src = @'
#include <omp.h>
#include <cstdio>

int main() {
    // Unsigned loop index and a max reduction: OpenMP 3.0 / 3.1, which
    // MSVC's /openmp (OpenMP 2.0) rejects and -openmp:llvm accepts.
    long long sum = 0;
    int mx = -1;
#pragma omp parallel for reduction(+ : sum) reduction(max : mx)
    for (unsigned i = 0; i < 1000u; ++i) {
        sum += i;
        if (static_cast<int>(i) > mx) mx = static_cast<int>(i);
    }
    int threads = 0;
#pragma omp parallel
    {
#pragma omp single
        threads = omp_get_num_threads();
    }
#ifdef _OPENMP
    long openmp = _OPENMP;
#else
    long openmp = 0;
#endif
    std::printf("openmp-smoke: _OPENMP=%ld sum=%lld max=%d threads=%d\n", openmp, sum, mx, threads);
    return (sum == 499500 && mx == 999 && threads == 4) ? 0 : 1;
}
'@
Set-Content -LiteralPath (Join-Path $smoke 'smoke.cpp') -Value $src -Encoding Ascii
Copy-Item -LiteralPath $dll -Destination $smoke   # beside the exe, as a consumer ships it

Push-Location $smoke
try {
    & cl /nologo /EHsc /O2 /MD /openmp:llvm "/I$inc" /c smoke.cpp /Fo:smoke.obj
    if ($LASTEXITCODE -ne 0) { throw 'openmp: smoke.cpp does not compile with cl -openmp:llvm against include\omp.h' }
    # Which runtime does cl ask for? Informational: it is the name the
    # consumer's /NODEFAULTLIB has to cancel.
    Write-Host '-- openmp: default libraries requested by a cl -openmp:llvm /MD object --'
    & dumpbin /nologo /directives smoke.obj | Select-String -Pattern 'DEFAULTLIB' | ForEach-Object { Write-Host "  $($_.Line.Trim())" }
    & cl /nologo /EHsc /O2 /MDd /openmp:llvm "/I$inc" /c smoke.cpp /Fo:smoke_mdd.obj
    if ($LASTEXITCODE -eq 0) {
        Write-Host '-- openmp: ... and by a /MDd (debug CRT) object --'
        & dumpbin /nologo /directives smoke_mdd.obj | Select-String -Pattern 'DEFAULTLIB' | ForEach-Object { Write-Host "  $($_.Line.Trim())" }
    }

    $env:OMP_NUM_THREADS = '4'
    # The recommended line (distinct name) is the one that must work. The
    # upstream name by full path is tried too and reported, not required.
    $variants = @(
        @{ Name = 'smoke-distinct'; Lib = $distinct; Required = $true },
        @{ Name = 'smoke-libomp';   Lib = $implib;   Required = $false }
    )
    foreach ($v in $variants) {
        $exe = "$($v.Name).exe"
        & cl /nologo smoke.obj "/Fe:$exe" /link $v.Lib /NODEFAULTLIB:libomp.lib
        $ok = ($LASTEXITCODE -eq 0)
        $deps = @()
        if ($ok) {
            $deps = @(& dumpbin /nologo /dependents $exe | ForEach-Object { $_.Trim() } |
                Where-Object { $_ -match '(?i)\.dll$' })
            Write-Host "openmp: $exe (links $(Split-Path -Leaf $v.Lib) by full path) imports: $($deps -join ', ')"
            $hasOmp = @($deps | Where-Object { $_ -ieq 'libomp.dll' }).Count -eq 1
            $hasMsvc = @($deps | Where-Object { $_ -match '(?i)^(libomp140|vcomp)' }).Count -gt 0
            $ok = $hasOmp -and (-not $hasMsvc)
        }
        if ($ok) {
            $out = & ".\$exe"
            $ok = ($LASTEXITCODE -eq 0)
            Write-Host "openmp: $exe -> $out"
        }
        if (-not $ok) {
            if ($v.Required) {
                throw "openmp: $exe failed -- linking $($v.Lib) by full path with /NODEFAULTLIB:libomp.lib must yield a working exe importing libomp.dll only"
            }
            Write-Host "openmp: NOTE: $exe failed (informational; consumers should link $distinctImplib)"
        }
    }
} finally {
    Pop-Location
    Remove-Item Env:OMP_NUM_THREADS -ErrorAction SilentlyContinue
}

Write-Host "openmp: OK -- bin\libomp.dll + lib\$distinctImplib (and lib\libomp.lib) + include\omp.h; a cl -openmp:llvm consumer links it and imports libomp.dll only"

Invoke-CvcRewriteInstallPaths
