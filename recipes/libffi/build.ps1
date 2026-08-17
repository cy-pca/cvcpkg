# recipes/libffi/build.ps1 — build libffi on Windows via MSYS2 + MinGW autotools.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# Override --prefix with a DRIVE-LETTER path.
#
# Invoke-CvcMsysAutotoolsBuild passes --prefix as an MSYS path (/c/Users/...),
# which is right for the MSYS-side tools but wrong for the MinGW toolchain:
# /mingw64/bin binaries are NATIVE Windows executables and do not translate
# POSIX paths, and MSYS_NO_PATHCONV=1 (set by the helper, deliberately) stops
# the shell from translating for them.
#
# For a shared build the helper configures --enable-shared --enable-static, so
# libtool installs libffi.a and then runs ranlib on it. That last step is where
# it broke on 2026-08-16:
#
#   libtool: install: ranlib /c/Users/.../install/lib/../lib/libffi.a
#   C:\msys64\mingw64\bin\ranlib.exe: '/c/Users/.../libffi.a': No such file
#
# The install itself succeeded — /usr/bin/install is an MSYS binary and
# understands the path — so only the native tool in the chain failed. Same
# shape as the ffmpeg SRC_PATH and TMPDIR problems: an MSYS shell driving a
# native toolchain has to keep POSIX paths away from the native tools.
#
# ConfigureArgs are appended after the helper's own flags and therefore win,
# so this overrides the prefix without touching the shared helper (which every
# other autotools recipe on Windows depends on).
$winPrefix = ($env:CVC_INSTALL_DIR -replace '\\', '/')

Invoke-CvcMsysAutotoolsBuild -ConfigureArgs @(
    '--disable-docs',
    "--prefix=$winPrefix",
    "--includedir=$winPrefix/include"
)

Invoke-CvcRewriteInstallPaths
