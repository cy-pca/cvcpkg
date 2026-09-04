# recipes/googletest/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# gtest_force_shared_crt makes googletest use the dynamic CRT (/MD, /MDd)
# rather than its default static one. Without it a shared-link consumer gets
# a CRT mismatch at link time -- googletest's own README calls this out as
# the standard fix on MSVC. env-windows.ps1 already sets
# CMAKE_MSVC_RUNTIME_LIBRARY for the link mode; this makes googletest respect
# it instead of overriding it.
Invoke-CvcCMakeBuild @(
    '-DBUILD_GMOCK=ON',
    '-DINSTALL_GTEST=ON',
    '-DCMAKE_CXX_STANDARD=17',
    '-Dgtest_force_shared_crt=ON'
)
