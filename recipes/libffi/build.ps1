# recipes/libffi/build.ps1 — build libffi on Windows with MSVC.
#
# libffi's own configure, with its msvcc.sh wrapper as the compiler, run under
# the MSYS2 shell: the route libffi's README gives for Visual C++, and the one
# CPython takes for the libffi it ships (PCbuild\prepare_libffi.bat).
#
# This used to be a MinGW-w64 gcc build. An MSVC program can link that DLL but
# does not agree with it on two things:
#   * long double. gcc's is 16 bytes, so the MinGW libffi-8.dll exports a
#     16-byte ffi_type_longdouble and passes long double by reference; MSVC's
#     long double is a double, passed in a register. CPython's _ctypes is MSVC,
#     so c_longdouble got the wrong size and every call with a long double
#     argument or result passed garbage. An MSVC build writes ffi.h with
#     FFI_TYPE_LONGDOUBLE == FFI_TYPE_DOUBLE, as CPython's own libffi has.
#   * the C runtime. The MinGW DLL imported msvcrt.dll, a second CRT next to the
#     UCRT every MSVC consumer uses. This one imports VCRUNTIME140 and the UCRT.
# The DLL keeps its name (libffi-8.dll) and its exports, so a consumer linked
# against the MinGW build (glib) loads this one unchanged.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$jobs = [int]$env:CVC_JOBS
if ($jobs -le 0) { $jobs = 1 }

# The shell and make come from the msys2 and make recipes (depends.build, staged
# in the build prefix). Get-CvcGitBash prefers the build prefix's msys2.
$bash       = Get-CvcGitBash
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
# Drive-letter form: make install hands it to native tools (cl, lib) too.
$winPrefix  = $env:CVC_INSTALL_DIR -replace '\\', '/'
$toolBins   = (@($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ } |
               Select-Object -Unique | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'
$pathFlag   = if ($toolBins) { "export PATH='${toolBins}:'`$PATH; " } else { '' }

# The MSYS subsystem runs configure and make; the Windows PATH is inherited so
# the MSVC tools env-windows.ps1 imported (cl, ml64, lib, dumpbin) resolve
# inside bash. MSYS's own link.exe (coreutils) sits ahead of MSVC's on that
# PATH, which is harmless: libtool links through msvcc.sh, i.e. `cl ... -link`,
# and cl runs the link.exe next to itself.
$env:MSYSTEM         = 'MSYS'
$env:MSYS2_PATH_TYPE = 'inherit'
$env:CHERE_INVOKING  = '1'
foreach ($v in 'CC', 'CXX', 'LD', 'AR', 'NM', 'RANLIB', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS') {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
}

$probe = & $bash -lc "$pathFlag command -v make >/dev/null && command -v cl >/dev/null && command -v ml64 >/dev/null && echo OK" |
    Select-Object -Last 1
if ("$probe".Trim() -ne 'OK') {
    throw 'libffi: make, cl or ml64 not found in the MSYS shell (need the msys2 and make build deps and an MSVC environment)'
}

# FFI_BUILDING_DLL makes ffi.h dllexport the API while building the DLL. A
# static library is built with FFI_STATIC_BUILD instead, and its installed ffi.h
# defines that for consumers below; without it an MSVC consumer would
# dllimport from a library that has no DLL.
if ($env:CVC_LINK -eq 'static') {
    $linkFlags = '--enable-static --disable-shared'
    $cppflags  = '-DFFI_STATIC_BUILD'
} else {
    $linkFlags = '--enable-shared --disable-static'
    $cppflags  = '-DFFI_BUILDING_DLL'
}

# The x86_64 cygwin triple is CPython's, and makes libtool take its MSVC-wrapper
# path (DLL named libffi-8.dll, the linker writes libffi-8.lib beside it).
$cc  = "$msysSource/msvcc.sh -m64"
$cmd = "$pathFlag cd '$msysSource' && ./configure " +
       "CC='$cc' CXX='$cc' LD=link CPP='cl -nologo -EP' CXXCPP='cl -nologo -EP' " +
       "CPPFLAGS='$cppflags' NM='dumpbin -symbols' STRIP=: " +
       "--build=x86_64-w64-cygwin --host=x86_64-w64-cygwin " +
       "--prefix='$winPrefix' --disable-docs $linkFlags"
Write-Host "libffi: bash -lc `"$cmd`""
& $bash -lc $cmd
if ($LASTEXITCODE -ne 0) {
    $cfgLog = Join-Path $env:CVC_SOURCE_DIR 'config.log'
    if (Test-Path $cfgLog) { Get-Content $cfgLog -Tail 80 }
    throw 'libffi: configure failed'
}

& $bash -lc "$pathFlag cd '$msysSource' && make -j $jobs"
if ($LASTEXITCODE -ne 0) { throw 'libffi: make failed' }

# Headers, libffi.pc and the man pages. Not `make install`: libtool's
# MSVC-wrapper install copies the DLL into lib\ and then looks for an import
# library named libffi.lib, while the linker wrote libffi-8.lib, so it fails.
# CPython copies the library out of .libs\ for the same reason.
& $bash -lc "$pathFlag cd '$msysSource' && make install-data"
if ($LASTEXITCODE -ne 0) { throw 'libffi: make install-data failed' }

$libs = Join-Path $env:CVC_SOURCE_DIR 'x86_64-w64-cygwin\.libs'
$bin  = Join-Path $env:CVC_INSTALL_DIR 'bin'
$lib  = Join-Path $env:CVC_INSTALL_DIR 'lib'
New-Item -ItemType Directory -Force -Path $bin, $lib | Out-Null
if ($env:CVC_LINK -eq 'static') {
    Copy-Item (Join-Path $libs 'libffi.lib') $lib -Force
    $msvcLib = 'libffi.lib'
    # Consumers of the static library must not dllimport from it.
    $ffiH = Join-Path $env:CVC_INSTALL_DIR 'include\ffi.h'
    $text = Get-Content -Raw -LiteralPath $ffiH
    Set-Content -NoNewline -LiteralPath $ffiH -Value ("#ifndef FFI_STATIC_BUILD`n#define FFI_STATIC_BUILD`n#endif`n" + $text)
} else {
    # The DLL goes to bin\ (where Windows consumers and the .pth CPython ships
    # look); libffi-8.lib is its import library, the name CPython links.
    Copy-Item (Join-Path $libs 'libffi-8.dll') $bin -Force
    Copy-Item (Join-Path $libs 'libffi-8.lib') $lib -Force
    $msvcLib = 'libffi-8.lib'
}
# libffi.pc says -lffi, which MSVC (and meson, for glib) looks up as ffi.lib.
Copy-Item (Join-Path $lib $msvcLib) (Join-Path $lib 'ffi.lib') -Force

Invoke-CvcRewriteInstallPaths
