# recipes/zlib/build.ps1 — build zlib from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DZLIB_BUILD_EXAMPLES=OFF',
    "-DINSTALL_PKGCONFIG_DIR=$env:CVC_INSTALL_DIR\lib\pkgconfig"
)

# ── GNU-named aliases, so MinGW consumers can link this ─────────────────────
#
# zlib's CMake build is MSVC here and installs MSVC names only: zlib.lib
# (import) and zlibstatic.lib (static). A MinGW toolchain resolves `-lz` by
# looking for libz.dll.a or libz.a and finds NEITHER, so every MinGW consumer
# fails with a message about zlib being missing while zlib is plainly
# installed:
#
#   ERROR: zlib requested but not found          (ffmpeg's configure)
#
# ffmpeg-cli is the first such consumer and was synthesising an import library
# for itself. That is the wrong place for it: the fix belongs once, here, where
# the naming decision is made — otherwise every future MinGW consumer
# rediscovers the same bug.
#
# ONLY the import library can be aliased. An import lib contains nothing but
# thunks and symbol stubs, so the rename is a pure naming fix and the actual
# code is reached through zlib1.dll at run time.
#
# zlibstatic.lib must NOT be aliased to libz.a. It holds real MSVC-COMPILED
# OBJECTS, and MSVC emits references to its own CRT support routines that a
# MinGW link has no way to satisfy:
#
#   libz.a(zlibstatic.dir/inftrees.c.obj): undefined reference to `__security_cookie'
#                                          undefined reference to `__GSHandlerCheck'
#                                          undefined reference to `__report_rangecheckfailure'
#
# Those are the /GS buffer-security-check symbols. A .lib and a .a are both
# COFF archives, so the rename "works" right up until the link, which is what
# makes this an attractive and wrong idea. A MinGW consumer that wants zlib
# statically needs a MinGW-built zlib, not a renamed MSVC one.
$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$aliases = @(
    @{ src = 'zlib.lib'; dst = 'libz.dll.a' }   # import lib for the DLL — safe
)
foreach ($a in $aliases) {
    $s = Join-Path $lib $a.src
    $d = Join-Path $lib $a.dst
    if ((Test-Path $s) -and (-not (Test-Path $d))) {
        Copy-Item $s $d
        Write-Host "zlib: aliased $($a.src) -> $($a.dst) for MinGW consumers"
    }
}

# Never leave a stale libz.a behind: an earlier revision of this recipe aliased
# zlibstatic.lib, and that file links happily and then fails at symbol
# resolution inside someone else's build.
$badStatic = Join-Path $lib 'libz.a'
if (Test-Path $badStatic) {
    Remove-Item -Force $badStatic
    Write-Host 'zlib: removed libz.a (MSVC objects cannot be linked by MinGW)'
}

# A shared build with no import alias leaves `-lz` broken for every MinGW
# consumer, and the failure surfaces far away in someone else's configure.
if ($env:CVC_LINK -ne 'static' -and -not (Test-Path (Join-Path $lib 'libz.dll.a'))) {
    throw 'zlib: libz.dll.a was not produced; MinGW consumers cannot link -lz'
}
