# recipes/log4cplus/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DLOG4CPLUS_BUILD_TESTING=OFF',
    '-DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF',
    '-DWITH_UNIT_TESTS=OFF',
    # Build the NARROW (char) variant, not the MSVC default UNICODE one. log4cplus
    # defaults UNICODE=ON on Windows and produces log4cplusU.lib with wchar_t
    # symbols, but the whole CVC ecosystem uses the char API (VolumeRover's
    # log4cplus_compat.h undefs UNICODE; libcvc's FindLog4cplus looks for
    # "log4cplus", not "log4cplusU") — so a consumer got LNK2001 on the narrow
    # basic_string<char> symbols. Non-Windows already defaults to narrow.
    '-DUNICODE=OFF'
)
