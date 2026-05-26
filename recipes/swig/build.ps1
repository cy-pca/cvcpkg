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
$swigUrl = "https://sourceforge.net/projects/swig/files/swigwin/swigwin-${swigVer}/swigwin-${swigVer}.zip/download"
$swigZip = Join-Path $env:CVC_BUILD_DIR "swigwin-${swigVer}.zip"
$swigDir = Join-Path $env:CVC_BUILD_DIR "swigwin-${swigVer}"

Write-Host "Downloading swigwin-${swigVer}..."
Invoke-WebRequest -Uri $swigUrl -OutFile $swigZip -UseBasicParsing
Expand-Archive -Path $swigZip -DestinationPath $env:CVC_BUILD_DIR -Force

# Stage into install prefix.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\share\swig\$swigVer" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\lib\cmake\SWIG" | Out-Null

Copy-Item "$swigDir\swig.exe" "$env:CVC_INSTALL_DIR\bin\swig.exe"
Copy-Item -Recurse "$swigDir\Lib\*" "$env:CVC_INSTALL_DIR\share\swig\$swigVer\" -Force

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
