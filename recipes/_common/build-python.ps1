# recipes/_common/build-python.ps1 — shared Windows build for the CPython
# recipes (python311, python312, python313, python313t); the counterpart of
# _common/build-python.sh.
#
# Dot-source it and call Invoke-CvcPythonBuild (-FreeThreaded for python313t):
#
#   . (Join-Path $SCRIPT_DIR "../_common/build-python.ps1")
#   Invoke-CvcPythonBuild
#
# CPython builds on Windows with MSVC through PCbuild, and PC\layout stages a
# python.org-style tree at the prefix root: python.exe, python3XX.dll, Lib\,
# DLLs\, include\, libs\python3XX.lib (what find_package(Python3
# Development.Embed) expects).  There is no python3.exe/pip3; the command is
# `python` (python3.13t.exe for the free-threaded build).
#
# Nothing is downloaded.  PCbuild\build.bat would run get_externals.bat, which
# fetches bzip2, sqlite, xz and zlib sources and PREBUILT libffi and openssl
# from GitHub (python/cpython-source-deps, cpython-bin-deps), and
# find_python.bat, which fetches a Python from nuget.org when it finds none.
# So MSBuild runs pcbuild.proj directly, with the properties build.bat would
# pass, and every external library is the recipe's cvcpkg dependency in
# CVC_DEPS_PREFIX: python-externals.props points PCbuild at them and
# python-externals.targets swaps the libraries PCbuild compiles from source
# (bzip2, xz, zlib, sqlite) for cvcpkg's.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env-windows.ps1')

function Invoke-CvcPythonBuild {
    param([switch]$FreeThreaded)

    if (-not $env:CVC_DEPS_PREFIX) { throw 'CVC_DEPS_PREFIX must be set (the openssl/zlib/bzip2/xz/sqlite/libffi deps)' }
    Set-Location $env:CVC_SOURCE_DIR

    # ── The Python that runs CPython's code generators ──────────────────────
    # PCbuild runs Tools\build scripts with a host Python on every build:
    # deepfreeze.py in 3.11 and 3.12, generate_global_objects.py in 3.13.
    # Name it, so PCbuild never calls find_python.bat, whose last resort is
    # downloading one from nuget.org.  It must be 3.10 or newer: a python.exe
    # on PATH (the Microsoft Store's stub prints a message instead and is
    # skipped), else what the py launcher picks, as find_python.bat would.
    # Looked up on the full PATH, before the trim below.  It is the one build
    # input that is not a cvcpkg package: a cvcpkg CPython would have to exist
    # before the first one could be built.
    $hostPython = $null
    $probe = 'import sys; print(sys.executable if sys.version_info >= (3, 10) else "")'
    $candidates = @(Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { , @($_.Source) })
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) { $candidates += , @($launcher.Source, '-3') }
    foreach ($c in $candidates) {
        $exe = & $c[0] @($c | Select-Object -Skip 1) -c $probe 2>$null
        if ($LASTEXITCODE -eq 0 -and "$exe".Trim()) { $hostPython = "$exe".Trim(); break }
    }
    if (-not $hostPython) {
        throw 'no Python 3.10+ (python.exe on PATH, or the py launcher); PCbuild needs one to run its code generators'
    }
    $env:PythonForBuild = "`"$hostPython`""
    Write-Host "PythonForBuild: $hostPython"

    # ── Trim PATH to essentials before building ─────────────────────────────
    # The workflow's "Install system build tools" step dumps the ENTIRE machine
    # PATH into GITHUB_PATH, bloating PATH to ~7.5 KB. cmd.exe has an ~8 KB
    # command-line limit, so when vcvars64 — or MSBuild's _decimal `ml64`
    # custom-build — extends PATH via `set PATH=...;%PATH%` it overflows ("The
    # input line is too long"), the extension silently fails, and the MASM
    # assembler dir never lands on the PATH cmd sees. THAT is why ml64 was "not
    # recognized" for _decimal's vcdiv64.asm despite being resolvable in pwsh
    # (pwsh has no such command-line limit).
    #
    # Rebuild a lean PATH from only the tools the CPython build needs; vcvars
    # then adds the MSVC toolset with plenty of headroom. Capture the tool dirs
    # from the full PATH *before* trimming it.
    $keep = @(Split-Path $hostPython)
    foreach ($tool in 'git.exe', 'cmake.exe') {
        $c = Get-Command $tool -ErrorAction SilentlyContinue
        if ($c) { $keep += (Split-Path $c.Source) }
    }
    $env:PATH = (@(
            "$env:SystemRoot\System32",
            "$env:SystemRoot",
            "$env:SystemRoot\System32\Wbem",
            "$env:SystemRoot\System32\WindowsPowerShell\v1.0"
        ) + $keep | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique) -join ';'
    Write-Host "DIAG lean PATH length: $($env:PATH.Length)"

    # ── Source the MSVC x64 developer environment (vcvars64) into THIS process ──
    # Provides cl/link, msbuild, dumpbin and the MASM assembler (ml64.exe).
    # env-windows.ps1's Import-CvcMsvcEnv early-returns when cl is already
    # present (true on CI via msvc-dev-cmd), so it would not have sourced vcvars
    # into the trimmed PATH — do it here. Uses the robust temp-.cmd technique
    # (avoids the fragile inline `cmd /c "..."` quoting).
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { $vswhere = Join-Path $env:ProgramFiles 'Microsoft Visual Studio\Installer\vswhere.exe' }
    $vsRoot = & $vswhere -latest -products '*' `
        -requires 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' `
        -property installationPath 2>$null | Select-Object -First 1
    if (-not $vsRoot) { throw 'vswhere found no VS install with the x64 VC tools' }
    $vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found under $vsRoot" }
    $helper = Join-Path ([System.IO.Path]::GetTempPath()) ("cvc-vcvars-{0}.cmd" -f ([guid]::NewGuid().ToString('N')))
    Set-Content -LiteralPath $helper -Value ("@echo off`r`ncall `"$vcvars`" >nul 2>&1`r`nset") -Encoding Ascii
    try { $dump = & cmd.exe /c $helper } finally { Remove-Item -LiteralPath $helper -ErrorAction SilentlyContinue }
    foreach ($line in $dump) {
        if ($line -match '^([^=]+)=(.*)$') {
            $n = $matches[1]; $v = $matches[2]
            if ($n -in @('_', 'PROMPT')) { continue }
            [Environment]::SetEnvironmentVariable($n, $v, 'Process')
        }
    }
    Write-Host "DIAG post-vcvars PATH length: $($env:PATH.Length)"
    Write-Host "DIAG ml64: $((Get-Command ml64.exe -ErrorAction SilentlyContinue).Source)"
    if (-not (Get-Command ml64.exe -ErrorAction SilentlyContinue)) {
        throw 'ml64.exe not resolvable after sourcing vcvars64 — _decimal would fail'
    }

    # CPython's PCbuild\python.props maps only VisualStudioVersion 15.0-17.0 to
    # a platform toolset and otherwise falls back to v140, so under Visual
    # Studio 2026 (18.0, GitHub's windows-latest image) MSBuild asked for the VS
    # 2015 tools and failed with MSB8020.  MSBuild reads environment variables
    # as properties and python.props only fills PlatformToolset when it is
    # empty, so name VS 2026's toolset here.  Visual Studio 2022 (the fleet,
    # windows-2022) is unaffected.
    if ($env:VisualStudioVersion -eq '18.0' -and -not $env:PlatformToolset) {
        $env:PlatformToolset = 'v145'
        Write-Host "Visual Studio 18: PlatformToolset=v145"
    }

    # ── 0. Deps CPython can use ─────────────────────────────────────────────
    # Earlier Windows bundles of four of them could not be: libffi was a MinGW
    # build (gcc's long double, no libffi-8.lib), libbz2.dll and sqlite3.dll
    # exported nothing, and libcrypto was built with uplink, which exits the
    # process on the first FILE* call (ssl's load_dh_params) from an executable
    # without OPENSSL_Applink.  Name the revision that fixed each, rather than
    # fail at the link or at run time.
    $depsLib = Join-Path $env:CVC_DEPS_PREFIX 'lib'
    $depsBin = Join-Path $env:CVC_DEPS_PREFIX 'bin'
    if (-not (Test-Path (Join-Path $depsLib 'libffi-8.lib'))) {
        throw 'deps: no lib\libffi-8.lib; libffi must be the MSVC build (3.7.1+cvc.6 or later)'
    }
    foreach ($c in @(@('libbz2.dll', 'BZ2_bzlibVersion', 'bzip2 1.0.8+cvc.6'),
                     @('sqlite3.dll', 'sqlite3_libversion', 'sqlite 3.49.1+cvc.6'))) {
        # An incrementally linked DLL lists "name = @ILT+n(name)", hence ( |$).
        if (-not ((& dumpbin /nologo /exports (Join-Path $depsBin $c[0])) -match " $($c[1])( |$)")) {
            throw "deps: $($c[0]) exports no $($c[1]); need $($c[2]) or later"
        }
    }
    $libcrypto = Get-ChildItem -LiteralPath $depsBin -Filter 'libcrypto-*.dll' | Select-Object -First 1
    if (-not $libcrypto) { throw 'deps: no bin\libcrypto-*.dll' }
    if (Select-String -LiteralPath $libcrypto.FullName -Pattern 'OPENSSL_Applink' -SimpleMatch -Quiet) {
        throw "deps: $($libcrypto.Name) was built with uplink; need openssl 3.4.1+cvc.9 or later (no-uplink)"
    }

    # ── 1. Release x64 through pcbuild.proj ─────────────────────────────────
    # The properties build.bat passes, minus the externals download.  No
    # tkinter (no tcl/tk here); no PGO (it reruns the test suite: slow, and
    # flaky in CI).  IncludeExternals=true builds the modules that need the
    # external libraries (_bz2, _lzma, _sqlite3, _ssl, _hashlib) and zlib into
    # python3XX.dll.
    $commonDir = $PSScriptRoot
    $msbuildArgs = @(
        'PCbuild\pcbuild.proj', '/t:Build', '/m', '/nologo', '/v:m', '/clp:summary',
        '/p:Configuration=Release', '/p:Platform=x64',
        '/p:IncludeExternals=true', '/p:IncludeCTypes=true',
        '/p:IncludeSSL=true', '/p:IncludeTkinter=false',
        "/p:DisableGil=$(if ($FreeThreaded) { 'true' } else { 'false' })",
        "/p:CvcDepsPrefix=$env:CVC_DEPS_PREFIX",
        "/p:ExternalProps=$(Join-Path $commonDir 'python-externals.props')",
        "/p:ForceImportAfterCppTargets=$(Join-Path $commonDir 'python-externals.targets')"
    )
    Write-Host "msbuild $($msbuildArgs -join ' ')"
    & msbuild @msbuildArgs
    if ($LASTEXITCODE -ne 0) { throw "PCbuild (msbuild pcbuild.proj) failed ($LASTEXITCODE)" }
    if (Test-Path (Join-Path $env:CVC_SOURCE_DIR 'externals')) {
        throw 'PCbuild created externals\ — something fetched CPython''s bundled externals'
    }

    # ── 2. The cvcpkg DLLs the modules import, next to them ─────────────────
    # _ssl/_hashlib import libssl/libcrypto, _ctypes libffi, _sqlite3 sqlite3,
    # _bz2 libbz2 and _lzma liblzma, all from the deps prefix's bin\.  Copy each
    # (and what it imports in turn) into the build directory, where PC\layout
    # puts every DLL into DLLs\ beside the modules — where python.org's build
    # ships its libcrypto/libssl/libffi/sqlite3 too.  The loader looks for an
    # extension module's dependencies in the module's own directory, so they
    # load under -S, in a venv, and in an embedding application, none of which
    # run the prefix's cvcpkg-dll-directories.pth.
    $buildDir = Join-Path $env:CVC_SOURCE_DIR 'PCbuild\amd64'
    # Import tables name DLLs in any case (bzip2's .def says LIBBZ2); copy the
    # file under its own name.  Hashtable keys compare case-insensitively.
    $depsDlls = @{}
    Get-ChildItem -LiteralPath $depsBin -Filter '*.dll' -File | ForEach-Object { $depsDlls[$_.Name] = $_ }
    $queue = [System.Collections.Generic.Queue[string]]::new()
    Get-ChildItem -LiteralPath $buildDir -File | Where-Object { $_.Extension -in '.pyd', '.dll', '.exe' } |
        ForEach-Object { $queue.Enqueue($_.FullName) }
    $copied = @{}
    while ($queue.Count -gt 0) {
        foreach ($line in (& dumpbin /nologo /dependents $queue.Dequeue())) {
            if ($line -notmatch '^\s+(\S+\.dll)\s*$') { continue }
            $dll = $depsDlls[$Matches[1]]
            if (-not $dll -or $copied.ContainsKey($dll.Name)) { continue }
            Copy-Item -LiteralPath $dll.FullName -Destination $buildDir -Force
            $copied[$dll.Name] = $true
            $queue.Enqueue((Join-Path $buildDir $dll.Name))
        }
    }
    Write-Host "cvcpkg DLLs for DLLs\: $(($copied.Keys | Sort-Object) -join ', ')"

    # ── 3. Stage a relocatable layout into the install prefix ───────────────
    # --preset-default brings the full stdlib: venv, ensurepip, idlelib,
    # lib2to3.  Without it the tree has no venv, so `python -m venv` fails.
    # --include-dev carries the headers + libs\python3XX.lib needed to embed.
    #
    # --include-stable stages python3.dll, the limited-API forwarder. Without it
    # the prefix carries only python3XX.dll, and EVERY abi3 extension module
    # fails to import with "DLL load failed while importing _x: The specified
    # module could not be found" — because python3.dll is in its import table
    # and is simply not there. The error names the extension, never the missing
    # forwarder, and CPython then reports a misleading "the Python version for
    # which X was compiled (3.12.10) is incompatible with the current
    # interpreter (3.12.10)". Measured 2026-08-16 on drjit 1.3.1 (cp312-abi3):
    # python3.dll was the ONLY unresolved import of the whole dependency set.
    #
    # --include-freethreaded stages the free-threaded binaries instead:
    # python3.13t.exe, python313t.dll, python3t.dll and the *.cp313t-win_amd64
    # modules.
    $layoutArgs = @('--copy', $env:CVC_INSTALL_DIR, '--preset-default', '--include-pip',
                    '--include-dev', '--include-stable', '--precompile')
    if ($FreeThreaded) {
        $layoutArgs += '--include-freethreaded'
        $built = Get-ChildItem -LiteralPath $buildDir -Filter 'python3.*t.exe' | Select-Object -First 1
        if (-not $built) { throw "no python3.Xt.exe in $buildDir" }
        $py = $built.FullName
    } else {
        $py = Join-Path $buildDir 'python.exe'
    }
    & $py PC\layout @layoutArgs
    if ($LASTEXITCODE -ne 0) { throw "PC\layout failed ($LASTEXITCODE)" }

    # ── 4. Teach the interpreter where the prefix's DLLs live ───────────────
    # Windows has no RPATH, and since Python 3.8 the loader no longer searches
    # PATH for an extension module's dependencies — only the DLL search
    # directories.  cvcpkg keeps shared libraries in <prefix>\bin, so every
    # extension that links one (pillow -> zlib1/jpeg62/tiff, numpy -> openblas,
    # h5py -> hdf5) imports with "DLL load failed while importing _x: The
    # specified module could not be found" no matter what PATH says. This is
    # the Windows counterpart of the $ORIGIN RUNPATH the POSIX builds patch in.
    #
    # A .pth is the one hook that runs at interpreter startup before any
    # import. The path is derived from the running interpreter's own prefix, so
    # the tree stays relocatable.
    $siteDir = Join-Path $env:CVC_INSTALL_DIR "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $siteDir | Out-Null
    $pth = @'
import os, sys; _b = os.path.join(sys.prefix, 'bin'); os.path.isdir(_b) and hasattr(os, 'add_dll_directory') and os.add_dll_directory(_b)
'@
    Set-Content -Path (Join-Path $siteDir "cvcpkg-dll-directories.pth") -Value $pth -Encoding ASCII

    # pip only.  3.11's ensurepip (PC\layout --include-pip) also lays down
    # setuptools 65.5.0; next to the setuptools-cp311 package that left two
    # setuptools dist-infos in Lib\site-packages, and the old one's
    # check_test_suite entry point broke every setup.py using test_suite=.
    # Same removal as _common/build-python.sh on the POSIX platforms; 3.12+
    # bundle no setuptools, so this finds nothing there.
    foreach ($name in @('setuptools', 'pkg_resources', '_distutils_hack', 'distutils-precedence.pth')) {
        $path = Join-Path $siteDir $name
        if (Test-Path $path) { Remove-Item -Recurse -Force $path }
    }
    Get-ChildItem -Path $siteDir -Directory -Filter 'setuptools-*.dist-info' | Remove-Item -Recurse -Force

    # ── 5. Smoke check the staged interpreter ───────────────────────────────
    # Isolated (-I), and nothing of the deps prefix is on its DLL search path,
    # so each module loads only with what the layout ships.  The versions are
    # cvcpkg's libraries, not CPython's bundled ones.
    $staged = if ($FreeThreaded) { Join-Path $env:CVC_INSTALL_DIR (Split-Path -Leaf $py) } else { Join-Path $env:CVC_INSTALL_DIR 'python.exe' }
    $check = @'
import os, sys, ssl, sqlite3, ctypes, lzma, bz2, zlib, hashlib, tempfile
assert ctypes.sizeof(ctypes.c_longdouble) == ctypes.sizeof(ctypes.c_double)
ctypes.CDLL("kernel32").GetTickCount()
# OpenSSL reads this through a FILE*: an uplink libcrypto exits the process here.
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "dh.pem"), "w").close()
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_dh_params(os.path.join(d, "dh.pem"))
    except (ssl.SSLError, OSError):
        pass  # an empty file is no DH parameters; getting here is the point
assert hashlib.sha256(b"").hexdigest().startswith("e3b0c442")
assert bz2.decompress(bz2.compress(b"x" * 100)) == b"x" * 100
assert lzma.decompress(lzma.compress(b"x" * 100)) == b"x" * 100
sqlite3.connect(":memory:").execute("select 1").fetchone()
ft = not getattr(sys, "_is_gil_enabled", lambda: True)()
print("cvcpkg: python %s%s ok; %s, sqlite %s, zlib %s" % (
    sys.version.split()[0], " (free-threaded)" if ft else "", ssl.OPENSSL_VERSION,
    sqlite3.sqlite_version, zlib.ZLIB_RUNTIME_VERSION))
'@
    & $staged -I -c $check
    if ($LASTEXITCODE -ne 0) { throw "staged python smoke check failed ($LASTEXITCODE)" }
    if ($FreeThreaded) {
        & $staged -I -c 'import sys; assert not sys._is_gil_enabled(), "GIL is enabled"'
        if ($LASTEXITCODE -ne 0) { throw 'staged python is not free-threaded' }
    }
}
