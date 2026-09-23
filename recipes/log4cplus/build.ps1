# recipes/log4cplus/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# log4cplus on MSVC builds ONE character variant per config: the UNICODE
# (wchar_t) build produces log4cplusU.lib, the narrow (char) build produces
# log4cplus.lib. Consumers are split — Qt apps that keep UNICODE want the wide
# lib, while the CVC ecosystem uses the char API (VolumeRover's
# log4cplus_compat.h undefs UNICODE; libcvc's FindLog4cplus searches for
# "log4cplus", not "log4cplusU"). A single variant leaves the other camp with
# LNK2001 on the missing-width symbols. So build BOTH into the one prefix, from
# separate build dirs, so either consumer links the lib it needs.
$common = @(
    '-DLOG4CPLUS_BUILD_TESTING=OFF',
    '-DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF',
    '-DWITH_UNIT_TESTS=OFF'
)
$origBuildDir = $env:CVC_BUILD_DIR
try {
    # UNICODE (wchar_t) — log4cplusU.lib. Preserves wide-char support.
    $env:CVC_BUILD_DIR = "$origBuildDir-unicode"
    Invoke-CvcCMakeBuild ($common + '-DUNICODE=ON')

    # Narrow (char) — log4cplus.lib for the CVC ecosystem's char API. Installed
    # LAST so the exported cmake config target is log4cplus::log4cplus (the
    # recipe's declared cmake_packages target); log4cplusU.lib from the first
    # pass stays on disk for wide consumers to link by name.
    $env:CVC_BUILD_DIR = "$origBuildDir-narrow"
    Invoke-CvcCMakeBuild ($common + '-DUNICODE=OFF')
}
finally {
    $env:CVC_BUILD_DIR = $origBuildDir
}
