# recipes/swig/build.ps1 — install SWIG on Windows.
#
# On Windows, SWIG upstream ships a prebuilt swigwin zip that contains
# the swig.exe binary and all runtime files. We adopt it directly
# rather than building from source.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$swigVer = '4.4.1'

# Download the prebuilt Windows distribution.
# Use curl.exe (built into Windows 10+) instead of Invoke-WebRequest because
# SourceForge returns an HTML mirror-picker page (not the actual zip) unless
# the client follows multiple redirects to a CDN mirror.  curl with -L handles
# this; Invoke-WebRequest with -UseBasicParsing does not (it stops at the
# first 200 response which is the HTML page).
$swigUrl = "https://downloads.sourceforge.net/project/swig/swigwin/swigwin-${swigVer}/swigwin-${swigVer}.zip"
$swigZip = Join-Path $env:CVC_BUILD_DIR "swigwin-${swigVer}.zip"
$swigDir = Join-Path $env:CVC_BUILD_DIR "swigwin-${swigVer}"

Write-Host "Downloading swigwin-${swigVer}..."
& curl.exe -sSL -o $swigZip $swigUrl
if ($LASTEXITCODE -ne 0) {
    throw "curl failed with exit code $LASTEXITCODE downloading $swigUrl"
}
$zipSize = (Get-Item $swigZip).Length
if ($zipSize -lt 1MB) {
    throw "swigwin zip is suspiciously small ($zipSize bytes) — likely an HTML mirror page rather than the archive"
}
Expand-Archive -Path $swigZip -DestinationPath $env:CVC_BUILD_DIR -Force

# Stage into install prefix.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\share\swig\$swigVer" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\lib\cmake\SWIG" | Out-Null

Copy-Item "$swigDir\swig.exe" "$env:CVC_INSTALL_DIR\bin\swig.exe"
Copy-Item -Recurse "$swigDir\Lib\*" "$env:CVC_INSTALL_DIR\share\swig\$swigVer\" -Force

# swigwin's swig.exe resolves its runtime library as <exedir>\Lib — that is
# compiled in, and `swig -swiglib` reports it verbatim. Staging the library
# only under share/swig/<ver> left the binary pointing at a directory that
# does not exist, so CMake's module-mode FindSWIG (which shells out to
# `swig -swiglib`) failed with "Could NOT find SWIG (missing: SWIG_DIR)" even
# with the bundle installed — every consumer had to set SWIG_LIB by hand.
# The generated SWIGConfig.cmake below only helps callers who reach SWIG via
# config mode; find_package(SWIG COMPONENTS python) does not. Stage the
# library where the binary actually looks, so the bundle is self-describing.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin\Lib" | Out-Null
Copy-Item -Recurse "$swigDir\Lib\*" "$env:CVC_INSTALL_DIR\bin\Lib\" -Force

# Generate CMake config.
@"
set(SWIG_EXECUTABLE "`${CMAKE_CURRENT_LIST_DIR}/../../../bin/swig.exe")
set(SWIG_DIR "`${CMAKE_CURRENT_LIST_DIR}/../../../share/swig/$swigVer")
set(SWIG_VERSION "$swigVer")
set(SWIG_FOUND TRUE)
"@ | Set-Content "$env:CVC_INSTALL_DIR\lib\cmake\SWIG\SWIGConfig.cmake"

@"
set(PACKAGE_VERSION "$swigVer")
if("`${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("`${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
"@ | Set-Content "$env:CVC_INSTALL_DIR\lib\cmake\SWIG\SWIGConfigVersion.cmake"
