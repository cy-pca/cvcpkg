# recipes/swig/build.ps1 — build SWIG from source on Windows (MSVC + CMake).
#
# This used to adopt upstream's prebuilt swigwin zip, downloaded from
# SourceForge during the build with whatever curl.exe was first on PATH.  In a
# builder's job that is cvcpkg's curl (the deps prefix comes first), which has
# no CA bundle, so the download failed ("curl: (60) SSL certificate problem",
# windows populate 2026-10-19), and the bundle was an upstream binary anyway.
# Now: the recipe's own pinned source, SWIG's CMake build, cvcpkg's bison
# (the parser is generated from the git tag's .y) and pcre2.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$deps = $env:CVC_DEPS_PREFIX
# cvcpkg's bison finds its skeletons through BISON_PKGDATADIR (as build.sh).
$env:BISON_PKGDATADIR = Join-Path $deps 'share\bison'

Invoke-CvcCMakeBuild @(
    "-DCMAKE_PREFIX_PATH=$deps",
    "-DBISON_EXECUTABLE=$(Join-Path $deps 'bin\bison.exe')",
    '-DWITH_PCRE=ON'
)

$swigExe = Join-Path $env:CVC_INSTALL_DIR 'bin\swig.exe'
if (-not (Test-Path $swigExe)) { throw "swig: no $swigExe after install" }
$swigVer = (& $swigExe -version | Select-String -Pattern 'SWIG Version (\S+)').Matches[0].Groups[1].Value
if (-not $swigVer) { throw "swig: could not read the version from swig -version" }
# On Windows SWIG's CMake build installs its runtime library as bin\Lib, which
# is where swig.exe looks (<exedir>\Lib) and what `swig -swiglib` reports, so
# CMake's module-mode FindSWIG finds it too.  (The swigwin bundle also carried
# a share\swig\<ver> copy; nothing on Windows reads it.)
$binLib = Join-Path $env:CVC_INSTALL_DIR 'bin\Lib'
if (-not (Test-Path (Join-Path $binLib 'swig.swg'))) { throw "swig: no runtime library at $binLib" }

# CMake config package, for callers that reach SWIG in config mode.
$cfgDir = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake\SWIG'
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
@"
set(SWIG_EXECUTABLE "`${CMAKE_CURRENT_LIST_DIR}/../../../bin/swig.exe")
set(SWIG_DIR "`${CMAKE_CURRENT_LIST_DIR}/../../../bin/Lib")
set(SWIG_VERSION "$swigVer")
set(SWIG_FOUND TRUE)
"@ | Set-Content (Join-Path $cfgDir 'SWIGConfig.cmake')

@"
set(PACKAGE_VERSION "$swigVer")
if("`${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("`${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
"@ | Set-Content (Join-Path $cfgDir 'SWIGConfigVersion.cmake')

# The library must run from the bundle: `swig -swiglib` answers from <exedir>.
$reported = (& $swigExe -swiglib | Select-Object -First 1).Trim()
if ($reported.TrimEnd('\','/') -ne $binLib.TrimEnd('\','/')) {
    throw "swig -swiglib reports '$reported', expected '$binLib'"
}
