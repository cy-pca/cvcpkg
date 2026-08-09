# recipes/mingw-w64-gcc/build.ps1 — stage the prebuilt WinLibs MinGW-w64 GCC.
#
# WinLibs ships a relocatable toolchain: gcc/g++/gfortran locate their own
# libexec, headers and sysroot relative to bin/, so staging into the prefix is
# the whole install.  Nothing is compiled here.
#
# Unlike every other windows recipe this one does NOT want vcvars64: it is the
# GNU toolchain, and an MSVC environment on top only risks INCLUDE/LIB from one
# toolchain leaking into the other.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }

Set-Location $env:CVC_SOURCE_DIR

New-Item -ItemType Directory -Force -Path $env:CVC_INSTALL_DIR | Out-Null

# WinLibs bundles GDB, GDB embeds Python for its scripting support, and the
# distribution therefore ships an ENTIRE CPython 3.9 — headers, stdlib, import
# libraries, the DLL, and pkg-config files. Staging that into a shared prefix
# poisons it, because the pkg-config files include the GENERIC name:
#
#   lib/pkgconfig/python3.pc  ->  Name: Python / Version: 3.9
#
# so any meson project resolving dependency('python3') gets 3.9 instead of the
# CPython 3.12 sitting right next to it. scipy did exactly that and then failed
# to link against libpython3.9.dll.a with a wall of
#   undefined reference to `__imp_PyType_FromMetaclass'
# because Cython had generated code for 3.12. Before that it failed on
#   include/python3.9/Python.h:44:10: fatal error: crypt.h: No such file
# since those headers are the POSIX build.
#
# Nothing in this toolchain needs any of it: it is there for people writing gdb
# extensions, and gdb itself carries its own copy. include/ and lib/ otherwise
# must stay — include/c++ is the libstdc++ header tree.
$bundledPython = @(
    'include\python3.9'
    'lib\python3.9'
    'lib\libpython3.9.a'
    'lib\libpython3.9.dll.a'
    'bin\libpython3.9.dll'
    'share\python'
    'lib\pkgconfig\python3.pc'
    'lib\pkgconfig\python-3.9.pc'
    'lib\pkgconfig\python3-embed.pc'
    'lib\pkgconfig\python-3.9-embed.pc'
)
foreach ($rel in $bundledPython) {
    $p = Join-Path $env:CVC_SOURCE_DIR $rel
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

# Assert rather than trust the list above: a future WinLibs release that moves
# or renames any of this must fail HERE, not by silently shadowing the real
# interpreter in whatever prefix this gets staged into.
$leaked = @(Get-ChildItem -Path $env:CVC_SOURCE_DIR -Recurse -Force -ErrorAction SilentlyContinue `
                -Include 'python3*.pc', 'python-3*.pc', 'libpython3*' |
            Select-Object -ExpandProperty FullName)
if ($leaked) {
    throw ("mingw-w64-gcc: the toolchain archive still carries a bundled Python " +
           "after pruning, which would shadow the real interpreter in a shared " +
           "prefix. Extend the prune list. Found:`n  " + ($leaked -join "`n  "))
}

# Move rather than copy so staging does not need a second 1.3 GB; fall back to a
# copy when source and install land on different volumes.
foreach ($d in 'bin', 'lib', 'libexec', 'include', 'share', 'x86_64-w64-mingw32') {
    if (-not (Test-Path $d)) { continue }
    $dest = Join-Path $env:CVC_INSTALL_DIR $d
    try {
        Move-Item -Path $d -Destination $dest -Force -ErrorAction Stop
    } catch {
        Copy-Item -Path $d -Destination $dest -Recurse -Force
    }
}

# Sanity check: the three compilers this package exists to provide must run.
# gfortran especially — it is the reason the recipe exists, and a WinLibs
# variant built without it would otherwise stage silently and fail later
# inside scipy's meson configure.
foreach ($exe in 'gcc', 'g++', 'gfortran') {
    $p = Join-Path $env:CVC_INSTALL_DIR "bin\$exe.exe"
    if (-not (Test-Path $p)) { throw "expected $exe.exe in the staged toolchain, missing: $p" }
    & $p --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$exe --version failed with exit code $LASTEXITCODE" }
}

# libstdc++ <thread> must actually COMPILE. It does not on the posix
# (winpthreads) flavour of this GCC: pthread_t is a struct and
# bits/std_thread.h compares native_handle_type with ==, so every translation
# unit including <thread> fails with "no match for 'operator=='". That surfaces
# hundreds of targets into a consumer's build — scipy hits it through HiGHS —
# and reads like a broken dependency. Prove it here, where the error can name
# the actual cause and the fix.
$probe = Join-Path $env:TEMP "cvc-thread-probe-$PID.cpp"
@'
#include <thread>
int main() {
    std::thread t([]{});
    bool same = (t.get_id() == std::this_thread::get_id());
    t.join();
    return same ? 1 : 0;
}
'@ | Set-Content -Path $probe -Encoding ASCII
& (Join-Path $env:CVC_INSTALL_DIR 'bin\g++.exe') -std=c++17 $probe -o "$probe.exe" 2>&1 | Out-Null
$threadOk = ($LASTEXITCODE -eq 0)
Remove-Item -Force -ErrorAction SilentlyContinue $probe, "$probe.exe"
if (-not $threadOk) {
    throw ("mingw-w64-gcc: this toolchain cannot compile libstdc++ <thread>. " +
           "That is the signature of the posix/winpthreads flavour; the pinned " +
           "artifact must be the MCF-threads build (winlibs-x86_64-mcf-seh-...).")
}

$ver = (& (Join-Path $env:CVC_INSTALL_DIR 'bin\gfortran.exe') -dumpversion)
Write-Host "mingw-w64-gcc staged to $env:CVC_INSTALL_DIR (gfortran $ver, libstdc++ <thread> OK)"
