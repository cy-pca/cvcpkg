# recipes/wasmtime/build.ps1 — stage pre-built Wasmtime C API on Windows.
$ErrorActionPreference = 'Stop'

$wasmtimeVer = '45.0.0'
$artifact    = "wasmtime-v${wasmtimeVer}-x86_64-windows-c-api.zip"
$url         = "https://github.com/bytecodealliance/wasmtime/releases/download/v${wasmtimeVer}/${artifact}"
$zipPath     = Join-Path $env:CVC_BUILD_DIR $artifact
$extractDir  = Join-Path $env:CVC_BUILD_DIR 'wasmtime-extracted'

Write-Host "Downloading ${artifact}..."
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

# The zip contains a top-level directory.
$src = Get-ChildItem $extractDir -Directory | Select-Object -First 1

# Stage headers.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\include\wasmtime" | Out-Null
Copy-Item "$($src.FullName)\include\wasmtime.h" "$env:CVC_INSTALL_DIR\include\"
Copy-Item "$($src.FullName)\include\wasm.h"     "$env:CVC_INSTALL_DIR\include\"
Copy-Item "$($src.FullName)\include\wasi.h"     "$env:CVC_INSTALL_DIR\include\"
if (Test-Path "$($src.FullName)\include\wasmtime") {
    Copy-Item "$($src.FullName)\include\wasmtime\*" "$env:CVC_INSTALL_DIR\include\wasmtime\" -Recurse -Force
}

# Stage libraries.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\lib" | Out-Null
Copy-Item "$($src.FullName)\lib\*" "$env:CVC_INSTALL_DIR\lib\" -Force

# Generate CMake config.
$cmakeDir = "$env:CVC_INSTALL_DIR\lib\cmake\wasmtime"
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null

@"
get_filename_component(_WASMTIME_PREFIX "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
add_library(wasmtime::wasmtime UNKNOWN IMPORTED)
find_library(_WASMTIME_LIB NAMES wasmtime PATHS "`${_WASMTIME_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(wasmtime::wasmtime PROPERTIES
    IMPORTED_LOCATION "`${_WASMTIME_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "`${_WASMTIME_PREFIX}/include"
)
set(wasmtime_FOUND TRUE)
unset(_WASMTIME_PREFIX)
unset(_WASMTIME_LIB)
"@ | Set-Content "$cmakeDir\wasmtimeConfig.cmake"

@"
set(PACKAGE_VERSION "$wasmtimeVer")
if("`${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("`${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
"@ | Set-Content "$cmakeDir\wasmtimeConfigVersion.cmake"

Write-Host "Wasmtime ${wasmtimeVer} C API staged to $env:CVC_INSTALL_DIR"
